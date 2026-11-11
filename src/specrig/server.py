from __future__ import annotations

import os
import re
import threading
import time
from abc import abstractmethod
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from typing import Any, ClassVar, TypeVar
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from . import run
from .database import Engine
from .docker import Docker, DockerError
from .gate import Gate
from .resource import Databases

MARK = "specrig "


@dataclass(frozen=True)
class Address:
    scheme: str
    host: str
    port: int
    user: str
    password: str
    database: str
    query: tuple[tuple[str, str], ...] = ()

    def __str__(self) -> str:
        credentials = quote(self.user, safe="") + (f":{quote(self.password, safe='')}" if self.password else "")
        query = f"?{urlencode(self.query)}" if self.query else ""
        return f"{self.scheme}://{credentials}@{self.host}:{self.port}/{quote(self.database, safe='')}{query}"


class Server(Engine):
    name: ClassVar[str]
    scheme: ClassVar[str]
    port: ClassVar[int]
    user: ClassVar[str]
    maintenance: ClassVar[str]
    environment: ClassVar[list[str]]

    def __init__(self, service: str, given: str | None, image: str, scheme: str) -> None:
        super().__init__()
        self._service = service
        self._given = given
        self._image = image
        self._admin = self._parse(given) if given else Address(self.scheme, "", 0, self.user, "", self.maintenance)
        stem = self._admin.database if self._admin.database not in ("", self.maintenance) else f"{service}_test"
        self.database = f"{stem}_{os.getpid()}"
        self._stem = stem
        self.gate = Gate(self._upstream)
        self.address = str(
            replace(self._admin, scheme=scheme, host="127.0.0.1", port=self.gate.port, database=self.database)
        )
        self._lock = threading.Lock()
        self._started = False
        self._connection: Any = None

    def _parse(self, url: str) -> Address:
        parts = urlsplit(url)
        if not parts.hostname:
            raise ValueError(f"a {self.name} address is a URL: {self.scheme}://user:password@host:port/database")
        return Address(
            parts.scheme or self.scheme,
            parts.hostname,
            parts.port or self.port,
            unquote(parts.username or self.user),
            unquote(parts.password or ""),
            unquote(parts.path.lstrip("/")),
            tuple(parse_qsl(parts.query)),
        )

    def _upstream(self) -> tuple[str, int]:
        self.start()
        return self._admin.host, self._admin.port

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if not self._given:
                self._admin = self._parse(self._container())
            try:
                with closing(self._patient(0 if self._given else 60)) as connection:
                    self._sweep(connection)
                    self._drop(connection, self.database)
                    self._create(connection, self.database, f"{MARK}{int(time.time())}")
            except Exception as e:
                raise RuntimeError(f"{self.name} at {self._admin.host}:{self._admin.port}: {e}") from e
            self._started = True
            run.ending(self._down)

    def _container(self) -> str:
        docker = Docker()
        labels = {"specrig": self.name, "specrig.image": self._image}
        try:
            found = next(iter(docker.containers(labels)), None)
            if found is None:
                name = f"specrig-{self.name}-" + re.sub(r"[^a-z0-9]+", "-", self._image.lower())
                try:
                    # With no password, a port on every interface is an open database on the laptop's network.
                    id = docker.run(self._image, self.port, self.environment, labels, name, loopback=True)
                except DockerError as e:
                    if e.status != 409:
                        raise
                    id = docker.containers(labels)[0]["Id"]
            else:
                id = found["Id"]
                if found["State"] != "running":
                    docker.start(id)
            port = docker.port(id, self.port)
        except DockerError as e:
            raise RuntimeError(
                f"{self.name}: no address is given and no container started — set {variable(self._service, self.name)}, "
                f"or start Docker: {e}"
            ) from e
        return f"{self.scheme}://{self.user}@{Docker.host()}:{port}/{self.maintenance}"

    # The image starts a server of its own on a socket while it initialises, so TCP
    # answers only once the real one is up.
    def _patient(self, patience: float) -> Any:
        deadline = time.monotonic() + patience
        while True:
            try:
                return self._connect(self.maintenance)
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    def _sweep(self, connection: Any) -> None:
        ours = re.compile(rf"^{re.escape(self._stem)}_[0-9]+$")
        hour_ago = time.time() - 3600
        for name, mark, connected in self._marks(connection):
            if ours.match(name) and connected == 0 and mark.startswith(MARK) and int(mark[len(MARK) :] or 0) < hour_ago:
                self._drop(connection, name)

    def _connected(self) -> Any:
        if self._connection is None or not self._alive(self._connection):
            self._connection = self._connect(self.database)
        return self._connection

    def down(self, databases: Databases) -> None:
        self.gate.refuse()

    def up(self) -> None:
        self.gate.reopen()

    def _down(self) -> None:
        self.gate.refuse()
        if self._connection is not None:
            self._connection.close()
        with closing(self._connect(self.maintenance)) as connection:
            self._drop(connection, self.database)

    @abstractmethod
    def _connect(self, database: str) -> Any: ...

    @abstractmethod
    def _alive(self, connection: Any) -> bool: ...

    @abstractmethod
    def _marks(self, connection: Any) -> Iterable[tuple[str, str, int]]: ...

    @abstractmethod
    def _create(self, connection: Any, name: str, mark: str) -> None: ...

    @abstractmethod
    def _drop(self, connection: Any, name: str) -> None: ...


S = TypeVar("S", bound=Server)

_servers: dict[tuple[str, str, str, str], Server] = {}


def served(kind: type[S], service: str, given: str | None, image: str, scheme: str) -> S:
    name = re.sub(r"[^a-z0-9]", "_", service.lower())
    given = given or os.environ.get(variable(name, kind.name))
    key = (kind.name, given or image, name, scheme)
    if key not in _servers:
        _servers[key] = kind(name, given, image, scheme)
    server = _servers[key]
    assert isinstance(server, kind)
    return server


def variable(service: str, name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", service.upper()) + f"_TEST_{name.upper()}"
