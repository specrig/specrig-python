from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from . import run
from .docker import Docker, DockerError
from .gate import Gate
from .resource import Resource

if TYPE_CHECKING:
    from .rig import Rig

_MARK = "specrig "


@dataclass(frozen=True)
class Address:
    scheme: str
    host: str
    port: int
    user: str
    password: str
    database: str
    query: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def parse(url: str) -> Address:
        parts = urlsplit(url)
        if not parts.hostname:
            raise ValueError("a postgres address is a URL: postgresql://user:password@host:port/database")
        return Address(
            parts.scheme or "postgresql",
            parts.hostname,
            parts.port or 5432,
            unquote(parts.username or "postgres"),
            unquote(parts.password or ""),
            unquote(parts.path.lstrip("/")),
            tuple(parse_qsl(parts.query)),
        )

    def __str__(self) -> str:
        credentials = quote(self.user, safe="") + (f":{quote(self.password, safe='')}" if self.password else "")
        query = f"?{urlencode(self.query)}" if self.query else ""
        return f"{self.scheme}://{credentials}@{self.host}:{self.port}/{quote(self.database, safe='')}{query}"

    def connect(self, database: str | None = None) -> Any:
        import psycopg

        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password or None,
            dbname=database or self.database,
            autocommit=True,
            connect_timeout=5,
        )


class _Server:
    def __init__(self, service: str, given: str | None, image: str, scheme: str) -> None:
        self._service = service
        self._given = given
        self._image = image
        self._admin = Address.parse(given) if given else Address("postgresql", "", 0, "postgres", "", "postgres")
        stem = self._admin.database if self._admin.database not in ("", "postgres") else f"{service}_test"
        self.database = f"{stem}_{os.getpid()}"
        self._stem = stem
        self.gate = Gate(self._upstream)
        self.address = str(
            replace(self._admin, scheme=scheme, host="127.0.0.1", port=self.gate.port, database=self.database)
        )
        self._lock = threading.Lock()
        self._started = False
        self._migration: BaseException | bool | None = None
        self._connection: Any = None

    def _upstream(self) -> tuple[str, int]:
        self.start()
        return self._admin.host, self._admin.port

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if not self._given:
                self._admin = Address.parse(self._container())
            maintenance = replace(self._admin, database="postgres")
            try:
                with _patient(maintenance, 0 if self._given else 60) as connection:
                    _sweep(connection, self._stem)
                    connection.execute(f"DROP DATABASE IF EXISTS {_quoted(self.database)} WITH (FORCE)")
                    connection.execute(f"CREATE DATABASE {_quoted(self.database)}")
                    connection.execute(f"COMMENT ON DATABASE {_quoted(self.database)} IS '{_MARK}{int(time.time())}'")
            except Exception as e:
                raise RuntimeError(f"postgres at {self._admin.host}:{self._admin.port}: {e}") from e
            self._started = True
            run.ending(self._down)

    def _container(self) -> str:
        docker = Docker()
        labels = {"specrig": "postgres", "specrig.image": self._image}
        try:
            found = next(iter(docker.containers(labels)), None)
            if found is None:
                name = "specrig-postgres-" + re.sub(r"[^a-z0-9]+", "-", self._image.lower())
                try:
                    id = docker.run(self._image, 5432, ["POSTGRES_HOST_AUTH_METHOD=trust"], labels, name)
                except DockerError as e:
                    if e.status != 409:
                        raise
                    id = docker.containers(labels)[0]["Id"]
            else:
                id = found["Id"]
                if found["State"] != "running":
                    docker.start(id)
            port = docker.port(id, 5432)
        except DockerError as e:
            variable = re.sub(r"[^A-Z0-9]", "_", self._service.upper()) + "_TEST_POSTGRES"
            raise RuntimeError(
                f"postgres: no address is given and no container started — set {variable}, or start Docker: {e}"
            ) from e
        return f"postgresql://postgres@{Docker.host()}:{port}/postgres"

    def once(self, migrate: Callable[[], None]) -> None:
        if self._migration is None:
            try:
                migrate()
                self._migration = False
            except Exception as e:
                self._migration = e
        if isinstance(self._migration, BaseException):
            raise RuntimeError(f"{self.database} did not migrate: {self._migration}") from self._migration

    # Found anew for every case: a service whose start creates its tables has none
    # before its first case.
    def reset(self, keep: Sequence[str]) -> None:
        found = self._connected().execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2"
        )
        tables = [f"{_quoted(schema)}.{_quoted(table)}" for schema, table in found.fetchall() if table not in keep]
        if tables:
            self._connected().execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")

    def _connected(self) -> Any:
        if self._connection is None or self._connection.closed:
            self._connection = self._admin.connect(self.database)
        return self._connection

    def _down(self) -> None:
        self.gate.refuse()
        if self._connection is not None:
            self._connection.close()
        with self._admin.connect("postgres") as connection:
            connection.execute(f"DROP DATABASE IF EXISTS {_quoted(self.database)} WITH (FORCE)")


_servers: dict[tuple[str, str, str], _Server] = {}


class Postgres(Resource):
    def __init__(
        self,
        setting: str,
        address: str | None = None,
        image: str = "postgres:17-alpine",
        keep: Sequence[str] = (),
        scheme: str = "postgresql",
    ) -> None:
        self.setting = setting
        self._given = address
        self._image = image
        self._keep = tuple(keep)
        self._scheme = scheme
        self._server: _Server | None = None
        self._rig: Rig | None = None
        self._started = False
        self._refusing = False
        self._error: BaseException | None = None

    def fresh(self) -> Postgres:
        return Postgres(self.setting, self._given, self._image, self._keep, self._scheme)

    def bind(self, service: str) -> str:
        return self._of(service).address

    def provision(self) -> None:
        self._held().start()

    def ready(self, rig: Rig) -> None:
        databases = rig.databases()
        server = self._held()
        server.once(databases.migrate)
        server.reset((*self._keep, *databases.bookkeeping()))
        self._rig = rig

    def started(self) -> None:
        self._started = True
        self._apply()

    # Refused, the database is down for every connection the service holds and every
    # one it makes; an error given instead fails every statement with it. A fault a
    # step sets before the boot waits for the service to start.
    def fail_with(self, error: BaseException | None = None) -> None:
        if error is None:
            self._refusing = True
        else:
            self._error = error
        if self._started:
            self._apply()

    def release(self) -> None:
        if self._server is not None:
            self._server.gate.reopen()
        if self._rig is not None and (self._refusing or self._error is not None):
            self._rig.databases().heal()
        self._rig = None
        self._started = False
        self._refusing = False
        self._error = None

    def _apply(self) -> None:
        assert self._rig is not None
        if self._refusing:
            self._held().gate.refuse()
        if self._error is not None:
            self._rig.databases().fail(self._held().database, self._error)

    def _of(self, service: str) -> _Server:
        if self._server is None:
            name = re.sub(r"[^a-z0-9]", "_", service.lower())
            given = self._given or os.environ.get(f"{name.upper()}_TEST_POSTGRES")
            key = (given or self._image, name, self._scheme)
            if key not in _servers:
                _servers[key] = _Server(name, given, self._image, self._scheme)
            self._server = _servers[key]
        return self._server

    def _held(self) -> _Server:
        if self._server is None:
            raise RuntimeError("postgres is not provisioned: the harness has not prepared the run")
        return self._server


# The image starts a server of its own on a socket while it initialises, so TCP
# answers only once the real one is up.
def _patient(address: Address, patience: float) -> Any:
    deadline = time.monotonic() + patience
    while True:
        try:
            return address.connect()
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def _sweep(connection: Any, stem: str) -> None:
    ours = re.compile(rf"^{re.escape(stem)}_[0-9]+$")
    hour_ago = time.time() - 3600
    found = connection.execute(
        "SELECT d.datname, coalesce(shobj_description(d.oid, 'pg_database'), ''),"
        " (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname) FROM pg_database d"
    )
    for name, mark, connected in found.fetchall():
        if ours.match(name) and connected == 0 and mark.startswith(_MARK) and int(mark[len(_MARK) :] or 0) < hour_ago:
            connection.execute(f"DROP DATABASE IF EXISTS {_quoted(name)} WITH (FORCE)")


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
