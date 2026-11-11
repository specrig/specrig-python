from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .database import Database, Write, trigger_events
from .server import Server, served

_BASELINE = "specrig_baseline"
_REFUSAL = "specrig_refusal"

_REFUSAL_SETUP = f"""
CREATE SCHEMA IF NOT EXISTS {_REFUSAL};
CREATE TABLE IF NOT EXISTS {_REFUSAL}.quota (
    relation oid,
    name text,
    remaining int NOT NULL,
    PRIMARY KEY (relation, name)
);
CREATE OR REPLACE FUNCTION {_REFUSAL}.refuse() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rest int;
BEGIN
    UPDATE {_REFUSAL}.quota SET remaining = remaining - 1
    WHERE relation = tg_relid AND name = tg_name
    RETURNING remaining INTO rest;
    IF rest < 0 THEN
        RAISE EXCEPTION 'specrig: % on % is refused', lower(tg_op), tg_table_name;
    END IF;
    IF tg_op = 'DELETE' THEN
        RETURN old;
    END IF;
    RETURN new;
END $$;
"""


class _Server(Server):
    name = "postgres"
    scheme = "postgresql"
    port = 5432
    user = "postgres"
    maintenance = "postgres"
    environment = ["POSTGRES_HOST_AUTH_METHOD=trust"]

    def __init__(self, service: str, given: str | None, image: str, scheme: str) -> None:
        super().__init__(service, given, image, scheme)
        self._restores: list[str] = []
        self._sequences: list[tuple[str, int, bool]] = []

    def _connect(self, database: str) -> Any:
        import psycopg

        return psycopg.connect(
            host=self._admin.host,
            port=self._admin.port,
            user=self._admin.user,
            password=self._admin.password or None,
            dbname=database,
            autocommit=True,
            connect_timeout=5,
        )

    def _alive(self, connection: Any) -> bool:
        return not connection.closed

    def _marks(self, connection: Any) -> Iterable[tuple[str, str, int]]:
        return connection.execute(
            "SELECT d.datname, coalesce(shobj_description(d.oid, 'pg_database'), ''),"
            " (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname) FROM pg_database d"
        ).fetchall()

    def _create(self, connection: Any, name: str, mark: str) -> None:
        connection.execute(f"CREATE DATABASE {_quoted(name)}")
        connection.execute(f"COMMENT ON DATABASE {_quoted(name)} IS '{mark}'")

    def _drop(self, connection: Any, name: str) -> None:
        connection.execute(f"DROP DATABASE IF EXISTS {_quoted(name)} WITH (FORCE)")

    def _remember(self, keep: Sequence[str]) -> None:
        connection = self._connected()
        connection.execute(f"DROP SCHEMA IF EXISTS {_BASELINE} CASCADE")
        for schema, table in self._tables(keep):
            name = f"{_quoted(schema)}.{_quoted(table)}"
            filled = connection.execute(
                f"SELECT NOT relispartition AND EXISTS (SELECT 1 FROM {name}) FROM pg_class WHERE oid = %s::regclass",
                (name,),
            )
            if filled.fetchone()[0]:
                insertable = connection.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = %s AND table_name = %s AND is_generated = 'NEVER' ORDER BY ordinal_position",
                    (schema, table),
                )
                columns = ", ".join(_quoted(column) for (column,) in insertable.fetchall())
                copy = f"{_BASELINE}.t{len(self._restores)}"
                connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_BASELINE}")
                connection.execute(f"CREATE TABLE {copy} AS SELECT {columns} FROM {name}")
                self._restores.append(
                    f"INSERT INTO {name} ({columns}) OVERRIDING SYSTEM VALUE SELECT {columns} FROM {copy}"
                )
            owned = connection.execute(
                "SELECT pg_get_serial_sequence(%s, column_name) FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = %s",
                (name, schema, table),
            )
            for (sequence,) in owned.fetchall():
                if sequence is not None:
                    stood = connection.execute(f"SELECT last_value, is_called FROM {sequence}").fetchone()
                    self._sequences.append((sequence, *stood))

    def refuse(self, table: str, rows: int, events: Sequence[str], commit: bool = False) -> None:
        relation = ".".join(_quoted(part) for part in table.split("."))
        name = "specrig_refuse_" + ("commit_" if commit else "") + "_".join(events)
        on = " OR ".join(events)
        trigger = (
            f"CREATE CONSTRAINT TRIGGER {name} AFTER {on} ON {relation} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
            if commit
            else f"CREATE TRIGGER {name} BEFORE {on} ON {relation} FOR EACH ROW"
        )
        connection = self._connected()
        try:
            with connection.transaction():
                connection.execute(_REFUSAL_SETUP)
                connection.execute(
                    f"INSERT INTO {_REFUSAL}.quota (relation, name, remaining) VALUES (%s::regclass, %s, %s)"
                    " ON CONFLICT (relation, name) DO UPDATE SET remaining = excluded.remaining",
                    (relation, name, rows),
                )
                connection.execute(f"DROP TRIGGER IF EXISTS {name} ON {relation}")
                connection.execute(f"{trigger} EXECUTE FUNCTION {_REFUSAL}.refuse()")
        except Exception as e:
            raise RuntimeError(f"cannot refuse writes to {table}: {e}") from e

    def lift(self) -> None:
        self._connected().execute(f"DROP SCHEMA IF EXISTS {_REFUSAL} CASCADE")

    def reset(self, keep: Sequence[str]) -> None:
        self.lift()
        tables = [f"{_quoted(schema)}.{_quoted(table)}" for schema, table in self._tables(keep)]
        if not tables:
            return
        connection = self._connected()
        with connection.transaction():
            if self._restores:
                try:
                    connection.execute("SET LOCAL session_replication_role = replica")
                except Exception as e:
                    raise RuntimeError(f"putting back the rows the migrations left needs a superuser: {e}") from e
            connection.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
            for restore in self._restores:
                connection.execute(restore)
            with connection.cursor() as cursor:
                cursor.executemany("SELECT setval(%s::regclass, %s, %s)", self._sequences)

    def _tables(self, keep: Sequence[str]) -> list[tuple[str, str]]:
        found = self._connected().execute(
            "SELECT schemaname, tablename FROM pg_tables"
            " WHERE schemaname NOT IN ('pg_catalog', 'information_schema', %s, %s) ORDER BY 1, 2",
            (_BASELINE, _REFUSAL),
        )
        return [(schema, table) for schema, table in found.fetchall() if table not in keep]


class Postgres(Database[_Server]):
    def __init__(
        self,
        setting: str,
        address: str | None = None,
        image: str = "postgres:17-alpine",
        keep: Sequence[str] = (),
        scheme: str = "postgresql",
    ) -> None:
        super().__init__(setting, keep)
        self._given = address
        self._image = image
        self._scheme = scheme

    def fresh(self) -> Postgres:
        return Postgres(self.setting, self._given, self._image, self._keep, self._scheme)

    def refuse_commit(self, table: str, *writes: Write) -> None:
        events = trigger_events(writes)
        self._refuse(lambda: self._held().refuse(table, 0, events, commit=True))

    def _of(self, service: str) -> _Server:
        if self._server is None:
            self._server = served(_Server, service, self._given, self._image, self._scheme)
        return self._server


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
