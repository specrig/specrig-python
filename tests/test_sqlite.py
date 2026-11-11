import asyncio
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from contextlib import closing
from typing import Any, Self

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

import specrig.sqlite
from specrig import SQLite, Write

SEEDED = """
create table states (id integer primary key autoincrement, name text not null);
insert into states (name) values ('new'), ('active'), ('closed');

create table categories (
    id integer primary key,
    parent_id integer references categories(id),
    name text not null,
    name_length int generated always as (length(name)) stored
);
insert into categories (parent_id, name) values (null, 'Master');
insert into categories (parent_id, name) values (1, 'Child');

create table vendors (
    id integer primary key,
    state_id int not null references states(id) on delete restrict,
    name text not null
);
insert into vendors (state_id, name) values (1, 'R1');

create table invoices (id integer primary key, vendor_id int not null references vendors(id));
insert into invoices (vendor_id) values (1);

create table audit (id integer primary key, what text not null);
create trigger vendors_audited after insert on vendors begin
    insert into audit (what) values (new.name);
end;
create trigger vendors_forgotten after delete on vendors begin
    insert into audit (what) values ('-' || old.name);
end;

create table orders (id integer primary key autoincrement, vendor_id int not null references vendors(id));
"""


class Harness:
    def __init__(self, service: str, schema: str, keep: Sequence[str] = (), scheme: str = "sqlite") -> None:
        self.database = SQLite("DATABASE_URL", keep=keep, scheme=scheme)
        self.address = self.database.bind(service)
        self.schema = schema
        self.failure: BaseException | None = None
        self.healed: list[str] = []
        self.database.provision()

    def case(self) -> None:
        self.database.release()
        self.database.ready(self)
        self.database.started()

    def databases(self) -> Self:
        return self

    def migrate(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(self.schema)

    def bookkeeping(self) -> list[str]:
        return []

    def fail(self, database: str, error: BaseException) -> None:
        self.healed.append(f"failing {database}")
        self.failure = error

    def heal(self) -> None:
        self.failure = None
        self.healed.append("healed")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.address.partition(":///")[2], isolation_level=None)
        connection.execute("pragma foreign_keys = on")
        return connection

    def sql(self, statement: str) -> list[Any]:
        with closing(self.connect()) as connection:
            return [row[0] for row in connection.execute(statement).fetchall()]

    def refused(self, statement: str) -> str:
        with pytest.raises(sqlite3.Error) as raised:
            self.sql(statement)
        return str(raised.value)


def migrated(service: str, schema: str = SEEDED, keep: Sequence[str] = (), scheme: str = "sqlite") -> Harness:
    harness = Harness(service, schema, keep, scheme)
    harness.case()
    return harness


def read(address: str, query: str) -> list[Any]:
    if "+aiosqlite" in address:

        async def reading() -> list[Any]:
            engine = create_async_engine(address)
            async with engine.connect() as connection:
                found = list((await connection.execute(text(query))).scalars())
            await engine.dispose()
            return found

        return asyncio.run(reading())
    engine = create_engine(address)
    with engine.connect() as connection:
        found = list(connection.execute(text(query)).scalars())
    engine.dispose()
    return found


def test_the_service_is_answered_with_a_file_of_the_runs_own() -> None:
    harness = Harness("answered", "", ())

    assert harness.address == f"sqlite:///{harness.database._held().database}"
    assert harness.address.startswith("sqlite:////")
    assert harness.address.endswith("/answered.db")


@pytest.mark.parametrize("scheme", ["sqlite", "sqlite+aiosqlite"])
def test_sqlalchemy_reaches_the_database_by_the_address_on_every_driver(scheme: str) -> None:
    harness = migrated(f"driver_{scheme.replace('+', '_')}", scheme=scheme)

    assert harness.address.startswith(f"{scheme}:////")
    assert read(harness.address, "select name from states order by id") == ["new", "active", "closed"]


def test_a_case_starts_with_the_rows_the_migrations_wrote_and_none_of_the_last_cases() -> None:
    harness = migrated("baseline_back")
    harness.sql("insert into states (name) values ('extra')")
    harness.sql("update vendors set name = 'renamed'")
    harness.sql("delete from categories where parent_id is not null")
    harness.sql("insert into orders (vendor_id) values (1)")

    harness.case()

    assert harness.sql("select name from states order by id") == ["new", "active", "closed"]
    assert harness.sql("select name from categories order by id") == ["Master", "Child"]
    assert harness.sql("select name from vendors order by id") == ["R1"]
    assert harness.sql("select id from orders") == []


def test_ids_go_on_after_the_rows_the_migrations_wrote_and_start_over_where_they_wrote_none() -> None:
    harness = migrated("baseline_ids")
    harness.sql("insert into states (name) values ('extra')")
    harness.sql("insert into orders (vendor_id) values (1)")

    harness.case()

    assert harness.sql("insert into states (name) values ('next') returning id") == [4]
    assert harness.sql("insert into vendors (state_id, name) values (1, 'R2') returning id") == [2]
    assert harness.sql("insert into orders (vendor_id) values (1) returning id") == [1]


def test_a_counter_moved_by_a_row_since_deleted_is_rewound() -> None:
    harness = migrated("baseline_counter")
    harness.sql("insert into states (name) values ('extra')")
    harness.sql("delete from states where name = 'extra'")

    harness.case()

    assert harness.sql("insert into states (name) values ('next') returning id") == [4]


def test_a_child_written_back_before_its_parent_and_a_tree_come_back() -> None:
    harness = migrated("baseline_order")
    harness.sql("update categories set name = 'Renamed' where id = 1")

    harness.case()

    assert harness.sql("select vendor_id from invoices") == [1]
    assert harness.sql("select parent_id from categories order by id") == [None, 1]


def test_a_tree_comes_back_on_a_sqlite_that_checks_foreign_keys_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = migrated("baseline_keys")
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    harness.sql("update categories set name = 'Renamed' where id = 1")
    connect = specrig.sqlite._File._connect

    def checking(file: Any) -> sqlite3.Connection:
        connection = connect(file)
        connection.execute("pragma foreign_keys = on")
        return connection

    monkeypatch.setattr(specrig.sqlite._File, "_connect", checking)

    harness.case()

    assert harness.sql("select name from vendors") == ["R1"]
    assert harness.sql("select parent_id from categories order by id") == [None, 1]


def test_a_virtual_table_and_its_shadow_tables_are_left_as_they_are() -> None:
    harness = migrated(
        "baseline_virtual",
        "create virtual table docs using fts5(body);"
        " insert into docs (body) values ('seeded');"
        " create table orders (id integer primary key);",
    )
    harness.sql("insert into docs (body) values ('the case''s')")
    harness.sql("insert into orders default values")

    harness.case()

    assert harness.sql("select id from orders") == []
    assert harness.sql("select body from docs order by rowid") == ["seeded", "the case's"]


def test_specrig_imports_on_a_python_built_without_sqlite() -> None:
    done = subprocess.run(
        [sys.executable, "-c", "import sys\nsys.modules['sqlite3'] = None\nimport specrig"],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr


def test_rows_written_back_fire_no_trigger_and_the_triggers_stay() -> None:
    harness = migrated("baseline_trigger")
    harness.sql("update vendors set name = 'renamed'")

    harness.case()

    assert harness.sql("select what from audit") == []
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    harness.sql("delete from vendors where name = 'R2'")
    assert harness.sql("select what from audit order by id") == ["R2", "-R2"]


def test_a_generated_column_is_computed_anew() -> None:
    harness = migrated("baseline_generated")
    harness.sql("update categories set name = 'Renamed' where id = 1")

    harness.case()

    assert harness.sql("select name_length from categories order by id") == [6, 5]


def test_migrations_that_write_no_rows_leave_nothing_aside() -> None:
    harness = migrated("baseline_empty", "create table orders (id integer primary key autoincrement)")
    harness.sql("insert into orders default values")

    harness.case()

    assert harness.sql("insert into orders default values returning id") == [1]


def test_a_kept_table_is_left_as_it_is() -> None:
    harness = migrated(
        "baseline_keep",
        "create table kept (id integer primary key autoincrement, name text not null);"
        " insert into kept (name) values ('migrated');",
        keep=["kept"],
    )
    harness.sql("insert into kept (name) values ('case')")

    harness.case()

    assert harness.sql("select name from kept order by id") == ["migrated", "case"]
    assert harness.sql("insert into kept (name) values ('next') returning id") == [3]


def test_a_table_the_service_creates_as_it_starts_is_emptied() -> None:
    harness = migrated("baseline_later")
    harness.sql("create table later (id integer primary key autoincrement, what text)")
    harness.sql("insert into later (what) values ('started')")

    harness.case()

    assert harness.sql("select what from later") == []
    assert harness.sql("insert into later default values returning id") == [1]


def test_a_refused_table_refuses_every_write_and_reads_go_on() -> None:
    harness = migrated("refuse_all")

    harness.database.refuse("vendors")

    assert harness.refused("insert into vendors (state_id, name) values (1, 'R2')") == (
        "specrig: insert on vendors is refused"
    )
    assert harness.refused("update vendors set name = 'renamed'") == "specrig: update on vendors is refused"
    assert harness.refused("delete from vendors") == "specrig: delete on vendors is refused"
    assert harness.refused("insert into vendors (state_id, name) values (1, 'R2') returning id").endswith("is refused")
    assert harness.sql("select name from vendors") == ["R1"]


def test_a_refusal_leaves_the_other_tables_alone() -> None:
    harness = migrated("refuse_elsewhere")

    harness.database.refuse("vendors")

    harness.sql("insert into states (name) values ('extra')")
    assert harness.sql("select name from states order by id")[-1] == "extra"


def test_a_refusal_leaves_the_writes_it_does_not_name_alone() -> None:
    harness = migrated("refuse_named")

    harness.database.refuse("vendors", Write.DELETE)

    harness.sql("update vendors set name = 'renamed'")
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.refused("delete from vendors") == "specrig: delete on vendors is refused"
    assert harness.sql("select name from vendors order by id") == ["renamed", "R2"]


def test_a_quota_is_shared_by_the_writes_it_names() -> None:
    harness = migrated("refuse_after")

    harness.database.refuse_after("states", 1)

    harness.sql("update states set name = 'renamed' where id = 1")
    assert harness.refused("insert into states (name) values ('second')") == "specrig: insert on states is refused"
    assert harness.sql("select name from states order by id") == ["renamed", "active", "closed"]


def test_a_rolled_back_row_gives_its_place_back() -> None:
    harness = migrated("refuse_rolled_back")
    harness.database.refuse_after("states", 1)

    with closing(harness.connect()) as connection:
        connection.execute("begin")
        connection.execute("insert into states (name) values ('undone')")
        connection.execute("rollback")

    harness.sql("insert into states (name) values ('kept')")
    assert harness.sql("select name from states order by id")[-1] == "kept"


def test_refusing_again_replaces_the_quota() -> None:
    harness = migrated("refuse_again")
    harness.database.refuse("states")

    harness.database.refuse_after("states", 1)

    harness.sql("insert into states (name) values ('first')")


def test_there_is_no_commit_to_refuse() -> None:
    assert not hasattr(SQLite("DATABASE_URL"), "refuse_commit")


def test_fail_with_fails_every_statement_as_a_file_that_does_not_open_until_heal() -> None:
    harness = migrated("refuse_heal")
    harness.sql("insert into states (name) values ('extra')")
    harness.database.refuse("vendors")

    harness.database.fail_with()

    assert isinstance(harness.failure, sqlite3.OperationalError)
    assert str(harness.failure) == "unable to open database file"
    assert harness.healed == [f"failing {harness.database._held().database}"]

    harness.database.heal()

    assert harness.failure is None
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select name from states order by id") == ["new", "active", "closed", "extra"]


def test_a_refusal_set_before_the_service_starts_waits_for_it() -> None:
    harness = migrated("refuse_late")
    harness.database.release()
    harness.database.refuse("vendors")

    harness.database.ready(harness)
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    harness.database.started()

    assert harness.refused("insert into vendors (state_id, name) values (1, 'R3')") == (
        "specrig: insert on vendors is refused"
    )


def test_the_reset_before_a_case_lifts_the_refusals_and_keeps_the_service_triggers() -> None:
    harness = migrated("refuse_reset")
    harness.database.refuse("vendors")

    harness.case()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select name from sqlite_master where name like 'specrig%' order by name") == []
    assert harness.sql("select what from audit") == ["R2"]


def test_the_reset_lifts_the_refusals_though_every_table_is_kept() -> None:
    harness = migrated("refuse_kept", "create table kept (id integer primary key)", keep=["kept"])
    harness.database.refuse("kept")

    harness.case()

    harness.sql("insert into kept default values")


def test_the_refusals_are_no_table_of_the_service() -> None:
    harness = migrated("refuse_hidden")

    harness.database.refuse("vendors")

    assert harness.sql("select count(*) from specrig_refusal") == [1]
    assert "specrig_refusal" not in harness.database._held()._tables(())


def test_only_a_write_can_be_refused() -> None:
    with pytest.raises(ValueError, match="'truncate' is not a write, refuse an insert, update or delete"):
        SQLite("DATABASE_URL").refuse("vendors", "truncate")  # type: ignore[arg-type]


def test_a_table_there_is_none_of_is_no_refusal() -> None:
    harness = migrated("refuse_nowhere")

    with pytest.raises(RuntimeError, match="cannot refuse writes to nowhere"):
        harness.database.refuse("nowhere")
