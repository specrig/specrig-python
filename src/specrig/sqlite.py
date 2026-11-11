from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from . import run
from .database import Database, Engine
from .resource import Databases

if TYPE_CHECKING:
    import sqlite3

_BASELINE = "specrig_baseline"
# In main beside the tables, because a trigger reads only its own schema.
_QUOTA = "specrig_refusal"


class _File(Engine):
    def __init__(self, service: str, scheme: str) -> None:
        super().__init__()
        self._dir = Path(tempfile.mkdtemp(prefix="specrig-"))
        self.database = str(self._dir / f"{service}.db")
        self.address = f"{scheme}:///{self.database}"
        self._kept = self._dir / "baseline.db"
        self._inserts: list[str] = []
        self._sequences: list[tuple[str, int]] = []
        self._connection: sqlite3.Connection | None = None
        run.ending(self._down)

    def start(self) -> None:
        pass

    def _remember(self, keep: Sequence[str]) -> None:
        tables = self._tables(keep)
        self._kept.unlink(missing_ok=True)
        with closing(self._attached()) as connection:
            for table in tables:
                if connection.execute(f"select exists(select 1 from main.{_quoted(table)})").fetchone()[0]:
                    insertable = connection.execute(
                        "select name from pragma_table_xinfo(?, 'main') where hidden = 0 order by cid", (table,)
                    )
                    columns = ", ".join(_quoted(column) for (column,) in insertable.fetchall())
                    kept = f"{_BASELINE}.{_quoted(table)}"
                    connection.execute(f"create table {kept} as select {columns} from main.{_quoted(table)}")
                    self._inserts.append(f"insert into main.{_quoted(table)} ({columns}) select {columns} from {kept}")
            if _sequenced(connection):
                self._sequences = connection.execute("select name, seq from main.sqlite_sequence").fetchall()

    def reset(self, keep: Sequence[str]) -> None:
        self.lift()
        tables = self._tables(keep)
        if not tables:
            return
        with closing(self._attached()) as connection:
            connection.execute("pragma foreign_keys = off")
            with _transaction(connection):
                triggers = connection.execute("select name, sql from main.sqlite_master where type = 'trigger'")
                kept = triggers.fetchall()
                for name, _ in kept:
                    connection.execute(f"drop trigger main.{_quoted(name)}")
                for table in tables:
                    connection.execute(f"delete from main.{_quoted(table)}")
                for insert in self._inserts:
                    connection.execute(insert)
                if _sequenced(connection):
                    connection.execute("delete from main.sqlite_sequence")
                    connection.executemany(
                        "insert into main.sqlite_sequence (name, seq) values (?, ?)", self._sequences
                    )
                for _, sql in kept:
                    connection.execute(sql)

    def refuse(self, table: str, rows: int, events: Sequence[str]) -> None:
        name = "specrig_refuse_" + "_".join(events)
        quota = f"relation = {_literal(table)} and name = {_literal(name)}"
        connection = self._connected()
        try:
            with _transaction(connection):
                connection.execute(
                    f"create table if not exists {_QUOTA}"
                    " (relation text, name text, remaining int not null, primary key (relation, name))"
                )
                connection.execute(
                    f"insert into {_QUOTA} (relation, name, remaining) values (?, ?, ?)"
                    " on conflict (relation, name) do update set remaining = excluded.remaining",
                    (table, name, rows),
                )
                for event in events:
                    trigger = _quoted(f"{name}_{event}_on_{table}")
                    connection.execute(f"drop trigger if exists {trigger}")
                    connection.execute(
                        f"create trigger {trigger} before {event} on {_quoted(table)} begin"
                        f" update {_QUOTA} set remaining = remaining - 1 where {quota};"
                        f" select raise(abort, {_literal(f'specrig: {event} on {table} is refused')})"
                        f" where (select remaining from {_QUOTA} where {quota}) < 0;"
                        " end"
                    )
        except Exception as e:
            raise RuntimeError(f"cannot refuse writes to {table}: {e}") from e

    def lift(self) -> None:
        connection = self._connected()
        with _transaction(connection):
            triggers = connection.execute(
                r"select name from main.sqlite_master where type = 'trigger' and name like 'specrig\_refuse\_%' escape '\'"
            )
            for (name,) in triggers.fetchall():
                connection.execute(f"drop trigger main.{_quoted(name)}")
            connection.execute(f"drop table if exists main.{_QUOTA}")

    def down(self, databases: Databases) -> None:
        import sqlite3

        databases.fail(self.database, sqlite3.OperationalError("unable to open database file"))

    def up(self) -> None:
        pass

    def _tables(self, keep: Sequence[str]) -> list[str]:
        found = self._connected().execute(
            r"select name from pragma_table_list where schema = 'main' and type = 'table'"
            r" and name not like 'sqlite\_%' escape '\' and name not like 'specrig\_%' escape '\' order by name"
        )
        return [table for (table,) in found.fetchall() if table not in keep]

    def _connected(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def _connect(self) -> sqlite3.Connection:
        import sqlite3

        return sqlite3.connect(self.database, isolation_level=None)

    def _attached(self) -> sqlite3.Connection:
        connection = self._connect()
        connection.execute(f"attach database ? as {_BASELINE}", (str(self._kept),))
        return connection

    def _down(self) -> None:
        if self._connection is not None:
            self._connection.close()
        shutil.rmtree(self._dir)


_files: dict[tuple[str, str], _File] = {}


class SQLite(Database[_File]):
    def __init__(self, setting: str, keep: Sequence[str] = (), scheme: str = "sqlite") -> None:
        super().__init__(setting, keep)
        self._scheme = scheme

    def fresh(self) -> SQLite:
        return SQLite(self.setting, self._keep, self._scheme)

    def _of(self, service: str) -> _File:
        if self._server is None:
            name = re.sub(r"[^a-z0-9]", "_", service.lower())
            key = (name, self._scheme)
            if key not in _files:
                _files[key] = _File(name, self._scheme)
            self._server = _files[key]
        return self._server


def _sequenced(connection: sqlite3.Connection) -> bool:
    found = connection.execute("select count(*) from main.sqlite_master where name = 'sqlite_sequence'")
    return bool(found.fetchone()[0])


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("begin immediate")
    try:
        yield
        connection.execute("commit")
    except BaseException:
        connection.execute("rollback")
        raise


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
