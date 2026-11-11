from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .database import Database
from .server import Server, served

_KEPT = "specrig_kept_"
_MARK = "specrig_mark"
_QUOTA = "specrig_refusal"


@dataclass(frozen=True)
class _Trigger:
    name: str
    table: str
    order: int
    sql_mode: str
    statement: str


class _Server(Server):
    name = "mysql"
    scheme = "mysql"
    port = 3306
    user = "root"
    maintenance = ""
    environment = ["MYSQL_ALLOW_EMPTY_PASSWORD=yes"]

    def __init__(self, service: str, given: str | None, image: str, scheme: str) -> None:
        super().__init__(service, given, image, scheme)
        self._inserts: dict[str, str] = {}
        self._checksums: dict[str, int] = {}
        self._increments: dict[str, int] = {}
        self._triggers: list[_Trigger] = []

    def _connect(self, database: str) -> Any:
        import pymysql

        return pymysql.connect(
            host=self._admin.host,
            port=self._admin.port,
            user=self._admin.user,
            password=self._admin.password,
            database=database or None,
            autocommit=True,
            connect_timeout=5,
            charset="utf8mb4",
            init_command="set session information_schema_stats_expiry = 0, session foreign_key_checks = 0,"
            " session sql_mode = concat_ws(',', nullif(@@session.sql_mode, ''), 'NO_AUTO_VALUE_ON_ZERO')",
        )

    def _alive(self, connection: Any) -> bool:
        import pymysql

        try:
            connection.ping(reconnect=False)
        except pymysql.MySQLError:
            return False
        return True

    def _marks(self, connection: Any) -> Iterable[tuple[str, str, int]]:
        return _rows(
            connection,
            "select s.schema_name, coalesce(t.table_comment, ''),"
            " (select count(*) from information_schema.processlist p where p.db = s.schema_name)"
            " from information_schema.schemata s left join information_schema.tables t"
            f" on t.table_schema = s.schema_name and t.table_name = '{_MARK}'",
        )

    def _create(self, connection: Any, name: str, mark: str) -> None:
        _run(connection, f"create database {_ticked(name)}")
        _run(connection, f"create table {_ticked(name)}.{_MARK} (id int) comment {_literal(mark)}")

    def _drop(self, connection: Any, name: str) -> None:
        import pymysql

        for (id,) in _rows(connection, "select id from information_schema.processlist where db = %s", (name,)):
            with contextlib.suppress(pymysql.MySQLError):
                _run(connection, f"kill {int(id)}")
        _run(connection, f"drop database if exists {_ticked(name)}")

    def _remember(self, keep: Sequence[str]) -> None:
        tables = self._tables(keep)
        connection = self._connected()
        for i, table in enumerate(tables):
            if _rows(connection, f"select exists(select 1 from {_ticked(table)})")[0][0]:
                insertable = _rows(
                    connection,
                    "select column_name from information_schema.columns where table_schema = database()"
                    " and table_name = %s and generation_expression = '' order by ordinal_position",
                    (table,),
                )
                columns = ", ".join(_ticked(column) for (column,) in insertable)
                kept = _ticked(f"{_KEPT}{i}")
                _run(connection, f"drop table if exists {kept}")
                _run(connection, f"create table {kept} as select {columns} from {_ticked(table)}")
                self._inserts[table] = f"insert into {_ticked(table)} ({columns}) select {columns} from {kept}"
        self._checksums = _checksums(connection, tables)
        self._increments = _increments(connection)
        self._triggers = [_trigger(connection, name, table, order) for name, table, _, order in _present(connection)]

    # Truncating, a trigger and a counter are DDL here: only what a case changed is put back.
    def reset(self, keep: Sequence[str]) -> None:
        self.lift()
        tables = self._tables(keep)
        if not tables:
            return
        connection = self._connected()
        checksums = _checksums(connection, tables)
        changed = [table for table in tables if checksums[table] != self._checksums.get(table, 0)]
        present = _present(connection)
        firing = []
        for name, table, event, order in present:
            if table not in changed:
                continue
            # A delete trigger fires only on rows there are to delete.
            if table in self._inserts if event == "INSERT" else event == "DELETE" and checksums[table] != 0:
                firing.append(_trigger(connection, name, table, order))
        names = {name for name, *_ in present}
        missing = [trigger for trigger in self._triggers if trigger.name not in names]
        dropped: list[_Trigger] = []
        try:
            for trigger in firing:
                _run(connection, f"drop trigger {_ticked(trigger.name)}")
                dropped.append(trigger)
            self._refill(connection, changed)
            self._rewind(connection, tables)
        except BaseException as e:
            _recreate(connection, [*dropped, *missing], e)
            raise
        _recreate(connection, [*dropped, *missing], None)

    def _refill(self, connection: Any, changed: list[str]) -> None:
        if not changed:
            return
        connection.begin()
        try:
            for table in changed:
                _run(connection, f"delete from {_ticked(table)}")
            for table in changed:
                if table in self._inserts:
                    _run(connection, self._inserts[table])
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _rewind(self, connection: Any, tables: list[str]) -> None:
        current = _increments(connection)
        for table in tables:
            stood = self._increments.get(table, 1)
            if table in current and current[table] != stood:
                _run(connection, f"alter table {_ticked(table)} auto_increment = {int(stood)}")

    def refuse(self, table: str, rows: int, events: Sequence[str]) -> None:
        name = "specrig_refuse_" + "_".join(events)
        quota = f"relation = {_literal(table)} and name = {_literal(name)}"
        connection = self._connected()
        try:
            _run(
                connection,
                f"create table if not exists {_QUOTA} (relation varchar(64) not null, name varchar(64) not null,"
                " remaining int not null, primary key (relation, name))",
            )
            _run(
                connection,
                f"insert into {_QUOTA} (relation, name, remaining) values (%s, %s, %s) as kept"
                " on duplicate key update remaining = kept.remaining",
                (table, name, rows),
            )
            for event in events:
                trigger = _ticked(_refusal(name, event, table))
                _run(connection, f"drop trigger if exists {trigger}")
                _run(
                    connection,
                    f"create trigger {trigger} before {event} on {_ticked(table)} for each row begin"
                    f" update {_QUOTA} set remaining = remaining - 1 where {quota};"
                    f" if (select remaining from {_QUOTA} where {quota}) < 0 then"
                    f" signal sqlstate '45000' set message_text = {_literal(f'specrig: {event} on {table} is refused')};"
                    " end if; end",
                )
        except Exception as e:
            raise RuntimeError(f"cannot refuse writes to {table}: {e}") from e

    def lift(self) -> None:
        connection = self._connected()
        triggers = _rows(
            connection,
            "select trigger_name from information_schema.triggers"
            " where trigger_schema = database() and trigger_name like 'specrig\\_refuse\\_%'",
        )
        for (trigger,) in triggers:
            _run(connection, f"drop trigger {_ticked(trigger)}")
        # Asked first because the drop is DDL even when there is nothing to drop.
        quotas = _rows(
            connection,
            f"select count(*) from information_schema.tables where table_schema = database() and table_name = '{_QUOTA}'",
        )
        if quotas[0][0]:
            _run(connection, f"drop table {_QUOTA}")

    def _tables(self, keep: Sequence[str]) -> list[str]:
        found = _rows(
            self._connected(),
            "select table_name from information_schema.tables where table_schema = database()"
            " and table_type = 'BASE TABLE' and table_name not like 'specrig\\_%' order by table_name",
        )
        return [table for (table,) in found if table not in keep]


class MySQL(Database[_Server]):
    def __init__(
        self,
        setting: str,
        address: str | None = None,
        image: str = "mysql:8.4",
        keep: Sequence[str] = (),
        scheme: str = "mysql",
    ) -> None:
        super().__init__(setting, keep)
        self._given = address
        self._image = image
        self._scheme = scheme

    def fresh(self) -> MySQL:
        return MySQL(self.setting, self._given, self._image, self._keep, self._scheme)

    def _of(self, service: str) -> _Server:
        if self._server is None:
            self._server = served(_Server, service, self._given, self._image, self._scheme)
        return self._server


# One statement for every table, a row each in the order they are named.
def _checksums(connection: Any, tables: list[str]) -> dict[str, int]:
    if not tables:
        return {}
    found = _rows(connection, "checksum table " + ", ".join(_ticked(table) for table in tables))
    return {table: checksum or 0 for table, (_, checksum) in zip(tables, found, strict=True)}


def _increments(connection: Any) -> dict[str, int]:
    found = _rows(
        connection,
        "select table_name, auto_increment from information_schema.tables"
        " where table_schema = database() and auto_increment is not null",
    )
    return {table: int(counter) for table, counter in found}


def _refusal(refusal: str, event: str, table: str) -> str:
    return "specrig_refuse_" + hashlib.blake2b(f"{refusal} {event} {table}".encode(), digest_size=8).hexdigest()


def _present(connection: Any) -> list[tuple[str, str, str, int]]:
    found = _rows(
        connection,
        "select trigger_name, event_object_table, event_manipulation, action_order from information_schema.triggers"
        " where trigger_schema = database() and trigger_name not like 'specrig\\_%'"
        " order by event_object_table, action_order",
    )
    return [(name, table, event, int(order)) for name, table, event, order in found]


def _trigger(connection: Any, name: str, table: str, order: int) -> _Trigger:
    _, sql_mode, statement, *_ = _rows(connection, f"show create trigger {_ticked(name)}")[0]
    return _Trigger(name, table, order, sql_mode, statement)


# The error that stopped a reset is the one to report: a trigger that fails after it is a note on it.
def _recreate(connection: Any, triggers: list[_Trigger], during: BaseException | None) -> None:
    import pymysql

    if not triggers:
        return
    failures = []
    try:
        mode = _rows(connection, "select @@session.sql_mode")[0][0]
        for trigger in sorted(triggers, key=lambda t: (t.table, t.order)):
            try:
                _run(connection, "set session sql_mode = %s", (trigger.sql_mode,))
                _run(connection, trigger.statement)
            except pymysql.MySQLError as e:
                failures.append(f"{trigger.name}: {e}")
        _run(connection, "set session sql_mode = %s", (mode,))
    except pymysql.MySQLError as e:
        failures.append(str(e))
    if not failures:
        return
    message = f"cannot create the triggers again: {'; '.join(failures)}"
    if during is None:
        raise RuntimeError(message)
    during.add_note(message)


def _rows(connection: Any, statement: str, parameters: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return list(cursor.fetchall())


def _run(connection: Any, statement: str, parameters: Sequence[Any] | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)


def _ticked(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


# A backslash escapes in a MySQL literal, unlike in the standard one.
def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
