import asyncio
import shutil
import threading
import time
from collections.abc import Sequence
from contextlib import closing
from typing import Any, Self
from urllib.parse import urlsplit

import pymysql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from specrig import MySQL, Write

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="mysql starts in Docker")

SEEDED = [
    "create table states (id int auto_increment primary key, name varchar(50) not null)",
    "insert into states (name) values ('new'), ('active'), ('closed')",
    """create table categories (
        id bigint auto_increment primary key,
        parent_id bigint,
        name varchar(50) not null,
        name_length int generated always as (char_length(name)) stored,
        created_at datetime(6) not null default current_timestamp(6),
        foreign key (parent_id) references categories(id)
    )""",
    "insert into categories (parent_id, name) values (null, 'Master')",
    "insert into categories (parent_id, name) values (1, 'Child')",
    """create table vendors (
        id int auto_increment primary key,
        state_id int not null,
        name varchar(50) not null,
        foreign key (state_id) references states(id) on delete restrict
    )""",
    "insert into vendors (state_id, name) values (1, 'R1')",
    """create table invoices (
        id int auto_increment primary key,
        vendor_id int not null,
        foreign key (vendor_id) references vendors(id)
    )""",
    "insert into invoices (vendor_id) values (1)",
    "create table audit (id int auto_increment primary key, what varchar(50) not null)",
    "create trigger vendors_audited after insert on vendors for each row insert into audit (what) values (new.name)",
    "create trigger vendors_forgotten after delete on vendors for each row"
    " insert into audit (what) values (concat('-', old.name))",
    """create table orders (
        id int auto_increment primary key,
        vendor_id int not null,
        foreign key (vendor_id) references vendors(id)
    )""",
]


class Harness:
    def __init__(
        self,
        service: str,
        schema: Sequence[str],
        keep: Sequence[str] = (),
        scheme: str = "mysql",
        address: str | None = None,
    ) -> None:
        self.database = MySQL("DATABASE_URL", address, keep=keep, scheme=scheme)
        self.address = self.database.bind(service)
        self.schema = schema
        self.failure: BaseException | None = None
        self.healed = 0
        self.database.provision()

    def case(self) -> None:
        self.database.release()
        self.database.ready(self)
        self.database.started()

    def databases(self) -> Self:
        return self

    def migrate(self) -> None:
        with self.connect() as connection, connection.cursor() as cursor:
            for statement in self.schema:
                cursor.execute(statement)

    def bookkeeping(self) -> list[str]:
        return []

    def fail(self, database: str, error: BaseException) -> None:
        self.failure = error

    def heal(self) -> None:
        self.failure = None
        self.healed += 1

    def connect(self) -> pymysql.Connection:
        parts = urlsplit(self.address)
        return pymysql.connect(
            host=parts.hostname,
            port=parts.port,
            user=parts.username,
            password=parts.password or "",
            database=parts.path.lstrip("/"),
            autocommit=True,
        )

    def sql(self, statement: str) -> list[Any]:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement)
            return [row[0] for row in cursor.fetchall()]

    def inserted(self, statement: str) -> int:
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement)
            return cursor.lastrowid

    def refused(self, statement: str) -> str:
        with pytest.raises(pymysql.MySQLError) as raised:
            self.sql(statement)
        return raised.value.args[1]


def migrated(service: str, schema: Sequence[str] = SEEDED, keep: Sequence[str] = (), scheme: str = "mysql") -> Harness:
    harness = Harness(service, schema, keep, scheme)
    harness.case()
    return harness


@pytest.mark.parametrize("driver", ["pymysql", "aiomysql", "asyncmy"])
def test_sqlalchemy_reaches_the_database_by_the_address_on_every_driver(driver: str) -> None:
    harness = migrated(f"driver_{driver}", scheme=f"mysql+{driver}")

    assert harness.address.startswith(f"mysql+{driver}://root@127.0.0.1:")
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

    assert harness.inserted("insert into states (name) values ('next')") == 4
    assert harness.inserted("insert into vendors (state_id, name) values (1, 'R2')") == 2
    assert harness.inserted("insert into orders (vendor_id) values (1)") == 1


def test_a_gap_the_migrations_left_is_kept() -> None:
    harness = migrated(
        "baseline_gap",
        [
            "create table states (id int auto_increment primary key, name varchar(50) not null)",
            "insert into states (name) values ('new'), ('active'), ('dropped')",
            "delete from states where name = 'dropped'",
        ],
    )
    harness.sql("insert into states (name) values ('extra')")

    harness.case()

    assert harness.inserted("insert into states (name) values ('next')") == 4


def test_a_counter_moved_by_a_row_since_deleted_is_rewound() -> None:
    harness = migrated("baseline_counter")
    harness.sql("insert into states (name) values ('extra')")
    harness.sql("delete from states where name = 'extra'")

    harness.case()

    assert harness.inserted("insert into states (name) values ('next')") == 4


def test_a_child_written_back_before_its_parent_and_a_tree_come_back() -> None:
    harness = migrated("baseline_order")
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    harness.sql("insert into invoices (vendor_id) values (2)")
    harness.sql("update categories set name = 'Renamed' where id = 1")

    harness.case()

    assert harness.sql("select vendor_id from invoices") == [1]
    assert harness.sql("select parent_id from categories order by id") == [None, 1]


def test_foreign_keys_are_checked_after_a_reset() -> None:
    harness = migrated("baseline_keys")
    harness.sql("insert into orders (vendor_id) values (1)")

    harness.case()

    assert "foreign key constraint fails" in harness.refused("insert into orders (vendor_id) values (99)")


def test_rows_written_back_fire_no_trigger_and_the_triggers_stay() -> None:
    harness = migrated("baseline_trigger")
    harness.sql("update vendors set name = 'renamed'")

    harness.case()

    assert harness.sql("select what from audit") == []
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    harness.sql("delete from vendors where name = 'R2'")
    assert harness.sql("select what from audit order by id") == ["R2", "-R2"]


def test_a_trigger_keeps_the_sql_mode_it_was_created_in() -> None:
    harness = migrated(
        "baseline_trigger_mode",
        [
            "create table vendors (id int auto_increment primary key, name varchar(50) not null)",
            "insert into vendors (name) values ('R1')",
            "create table audit (id int auto_increment primary key, what varchar(50) not null)",
            "set session sql_mode = 'PIPES_AS_CONCAT'",
            "create trigger vendors_audited after insert on vendors for each row"
            " insert into audit (what) values ('+' || new.name)",
        ],
    )
    harness.sql("delete from vendors")

    harness.case()

    harness.sql("insert into vendors (name) values ('R2')")
    assert harness.sql("select what from audit") == ["+R2"]


def test_triggers_created_again_fire_in_the_order_they_were_given() -> None:
    harness = migrated(
        "baseline_trigger_order",
        [
            "create table vendors (id int auto_increment primary key, name varchar(50) not null)",
            "insert into vendors (name) values ('R1')",
            "create table audit (id int auto_increment primary key, what varchar(50) not null)",
            "create trigger z_first after insert on vendors for each row insert into audit (what) values ('first')",
            "create trigger a_second after insert on vendors for each row follows z_first"
            " insert into audit (what) values ('second')",
            "create trigger m_before after insert on vendors for each row precedes z_first"
            " insert into audit (what) values ('before')",
        ],
    )
    harness.sql("delete from vendors")

    harness.case()

    harness.sql("insert into vendors (name) values ('R2')")
    assert harness.sql("select what from audit order by id") == ["before", "first", "second"]


def test_a_trigger_dropped_by_a_case_is_there_again_in_the_next() -> None:
    harness = migrated("baseline_trigger_dropped")
    harness.sql("drop trigger vendors_audited")

    harness.case()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select what from audit") == ["R2"]


def test_a_row_the_migrations_wrote_with_id_0_comes_back_with_it() -> None:
    harness = migrated(
        "baseline_zero",
        [
            "create table states (id int auto_increment primary key, name varchar(50) not null)",
            "insert into states (name) values ('none'), ('new')",
            "update states set id = 0 where name = 'none'",
        ],
    )
    harness.sql("insert into states (name) values ('extra')")

    harness.case()

    assert harness.sql("select id from states order by id") == [0, 2]


def test_a_reset_reconnects_where_the_server_closed_the_harnesss_connection() -> None:
    harness = migrated("baseline_reconnect")
    with harness.connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"kill {harness.database._held()._connected().thread_id()}")
    harness.sql("insert into states (name) values ('extra')")

    harness.case()

    assert harness.sql("select name from states order by id") == ["new", "active", "closed"]


def test_a_server_whose_user_has_a_password_is_reached_without_tls() -> None:
    root = migrated("password_root", ["select 1"])
    admin = root.database._held()._admin
    with closing(root.database._held()._connect("")) as connection, connection.cursor() as cursor:
        cursor.execute("drop user if exists 'specrig_password'@'%'")
        cursor.execute("create user 'specrig_password'@'%' identified with caching_sha2_password by 'secret'")
        cursor.execute("grant all on *.* to 'specrig_password'@'%'")
    harness = Harness(
        "password_user",
        ["create table states (id int primary key)"],
        address=f"mysql://specrig_password:secret@{admin.host}:{admin.port}/",
    )

    harness.case()

    assert harness.sql("select count(*) from states") == [0]


def test_a_trigger_on_a_table_the_case_left_alone_is_not_created_again() -> None:
    harness = migrated("baseline_trigger_alone")
    created = "select created from information_schema.triggers where trigger_name = 'vendors_audited'"
    before = harness.sql(created)
    harness.sql("insert into states (name) values ('extra')")
    time.sleep(0.02)

    harness.case()

    assert harness.sql(created) == before


def test_columns_the_server_fills_come_back_as_they_were() -> None:
    harness = migrated("baseline_generated")
    created = harness.sql("select created_at from categories order by id")
    harness.sql("update categories set name = 'Renamed' where id = 1")

    harness.case()

    assert harness.sql("select name_length from categories order by id") == [6, 5]
    assert harness.sql("select created_at from categories order by id") == created


def test_migrations_that_write_no_rows_leave_nothing_aside() -> None:
    harness = migrated("baseline_empty", ["create table orders (id int auto_increment primary key)"])
    harness.sql("insert into orders values ()")

    harness.case()

    assert harness.inserted("insert into orders values ()") == 1
    assert harness.sql(
        "select count(*) from information_schema.tables"
        " where table_schema = database() and table_name like 'specrig\\_kept\\_%'"
    ) == [0]


def test_a_kept_table_is_left_as_it_is() -> None:
    harness = migrated(
        "baseline_keep",
        [
            "create table kept (id int auto_increment primary key, name varchar(50) not null)",
            "insert into kept (name) values ('migrated')",
        ],
        keep=["kept"],
    )
    harness.sql("insert into kept (name) values ('case')")

    harness.case()

    assert harness.sql("select name from kept order by id") == ["migrated", "case"]
    assert harness.inserted("insert into kept (name) values ('next')") == 3


def test_a_trigger_on_a_kept_table_takes_no_part_in_the_reset() -> None:
    harness = migrated(
        "baseline_keep_trigger",
        [
            "create table kept (id int auto_increment primary key, name varchar(50) not null)",
            "insert into kept (name) values ('migrated')",
            "create table audit (id int auto_increment primary key, what varchar(50) not null)",
            "create trigger kept_forgotten after delete on kept for each row insert into audit (what) values (old.name)",
        ],
        keep=["kept"],
    )
    harness.sql("insert into audit (what) values ('case')")

    harness.case()

    assert harness.sql("select what from audit") == []
    assert harness.sql("select name from kept") == ["migrated"]


def test_a_table_the_service_creates_as_it_starts_is_emptied() -> None:
    harness = migrated("baseline_later")
    harness.sql("create table later (id int auto_increment primary key, what varchar(50))")
    harness.sql("insert into later (what) values ('started')")

    harness.case()

    assert harness.sql("select what from later") == []
    assert harness.inserted("insert into later (what) values ('next')") == 1


def test_a_refused_table_refuses_every_write_and_reads_go_on() -> None:
    harness = migrated("refuse_all")

    harness.database.refuse("vendors")

    assert harness.refused("insert into vendors (state_id, name) values (1, 'R2')") == (
        "specrig: insert on vendors is refused"
    )
    assert harness.refused("update vendors set name = 'renamed'") == "specrig: update on vendors is refused"
    assert harness.refused("delete from vendors") == "specrig: delete on vendors is refused"
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

    with harness.connect() as connection, connection.cursor() as cursor:
        connection.begin()
        cursor.execute("insert into states (name) values ('undone')")
        connection.rollback()

    harness.sql("insert into states (name) values ('kept')")
    assert harness.sql("select name from states order by id")[-1] == "kept"


def test_refusing_again_replaces_the_quota() -> None:
    harness = migrated("refuse_again")
    harness.database.refuse("states")

    harness.database.refuse_after("states", 1)

    harness.sql("insert into states (name) values ('first')")


def test_a_table_with_the_longest_name_can_be_refused() -> None:
    table = "t" * 64
    harness = migrated("refuse_long", [f"create table {table} (id int auto_increment primary key)"])

    harness.database.refuse(table)

    assert harness.refused(f"insert into {table} values ()") == f"specrig: insert on {table} is refused"


def test_there_is_no_commit_to_refuse() -> None:
    assert not hasattr(MySQL("DATABASE_URL"), "refuse_commit")


def test_fail_with_cuts_every_connection_and_heal_lifts_it_with_the_refusals_and_keeps_the_rows() -> None:
    harness = migrated("refuse_heal")
    harness.sql("insert into states (name) values ('extra')")
    harness.database.refuse("vendors")
    held = harness.connect()
    harness.database.fail_with()
    with pytest.raises(pymysql.OperationalError):
        harness.sql("select 1")
    with pytest.raises(pymysql.OperationalError), held.cursor() as cursor:
        cursor.execute("select 1")

    harness.database.heal()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select name from states order by id") == ["new", "active", "closed", "extra"]
    assert harness.healed == 1


def test_heal_ends_an_error_given_to_fail_with() -> None:
    harness = migrated("refuse_heal_error")
    harness.database.fail_with(RuntimeError("the disk is full"))
    assert harness.failure is not None

    harness.database.heal()

    assert harness.failure is None


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


def test_the_reset_before_a_case_lifts_the_refusals() -> None:
    harness = migrated("refuse_reset")
    harness.database.refuse("vendors")

    harness.case()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql(
        "select count(*) from information_schema.tables where table_schema = database() and table_name = 'specrig_refusal'"
    ) == [0]
    assert harness.sql(
        "select count(*) from information_schema.triggers where trigger_schema = database() and trigger_name like 'specrig%'"
    ) == [0]


def test_the_refusals_are_no_table_of_the_service() -> None:
    harness = migrated("refuse_hidden")

    harness.database.refuse("vendors")

    assert harness.sql("select count(*) from specrig_refusal") == [1]
    assert "specrig_refusal" not in harness.database._held()._tables(())


def test_only_a_write_can_be_refused() -> None:
    with pytest.raises(ValueError, match="'truncate' is not a write, refuse an insert, update or delete"):
        MySQL("DATABASE_URL").refuse("vendors", "truncate")  # type: ignore[arg-type]


def test_a_table_there_is_none_of_is_no_refusal() -> None:
    harness = migrated("refuse_nowhere")

    with pytest.raises(RuntimeError, match="cannot refuse writes to nowhere"):
        harness.database.refuse("nowhere")


def test_a_database_a_killed_run_left_over_an_hour_ago_is_dropped_and_a_younger_one_kept() -> None:
    harness = migrated("sweep", ["select 1"])
    server = harness.database._held()
    with closing(server._connect("")) as connection:
        for name, age in (("sweep_test_1", 7200), ("sweep_test_2", 60)):
            server._drop(connection, name)
            server._create(connection, name, f"specrig {int(time.time()) - age}")

        server._sweep(connection)

    assert harness.sql(
        "select schema_name from information_schema.schemata where schema_name like 'sweep\\_test\\__'"
    ) == ["sweep_test_2"]


def test_the_drop_does_not_wait_for_a_transaction_left_open() -> None:
    harness = migrated("drop_open")
    server = harness.database._held()
    held = harness.connect()
    with held.cursor() as cursor:
        held.begin()
        cursor.execute("select * from states")

    dropping = threading.Thread(target=lambda: _drop(server, server.database))
    dropping.start()
    dropping.join(10)

    assert not dropping.is_alive()
    with closing(server._connect("")) as connection, connection.cursor() as cursor:
        cursor.execute("select count(*) from information_schema.schemata where schema_name = %s", (server.database,))
        assert cursor.fetchone()[0] == 0


def read(address: str, query: str) -> list[Any]:
    if address.partition("+")[2].startswith(("aio", "async")):

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


def _drop(server: Any, name: str) -> None:
    with closing(server._connect("")) as connection:
        server._drop(connection, name)
