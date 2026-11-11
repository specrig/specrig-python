import shutil
from collections.abc import Sequence
from typing import Any, Self
from urllib.parse import urlsplit

import psycopg
import pytest
import sqlalchemy
from sqlalchemy import create_engine, event, text

import specrig.fastapi
from specrig import Postgres, Write

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="postgres starts in Docker")

SEEDED = """
create table states (id int generated always as identity primary key, name text not null);
insert into states (name) values ('new'), ('active'), ('closed');

create table categories (
    id bigserial primary key,
    parent_id bigint references categories(id),
    name text not null,
    name_length int generated always as (length(name)) stored
);
insert into categories (parent_id, name) values (null, 'Master'), (1, 'Child');

create table vendors (id serial primary key, state_id int not null references states(id), name text not null);
insert into vendors (state_id, name) values (1, 'R1');

create schema billing;
create table billing.invoices (id serial primary key, vendor_id int not null references vendors(id));
insert into billing.invoices (vendor_id) values (1);

create table audit (id serial primary key, what text not null);
create function audited() returns trigger language plpgsql as $$
begin
    insert into audit (what) values (new.name);
    return new;
end $$;
create trigger vendors_audited after insert on vendors for each row execute function audited();

create table orders (id serial primary key, vendor_id int not null references vendors(id));
"""


class Harness:
    def __init__(self, service: str, schema: str, keep: Sequence[str] = (), address: str | None = None) -> None:
        self.database = Postgres("DATABASE_URL", address, keep=keep)
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
        self.sql(self.schema)

    def bookkeeping(self) -> list[str]:
        return []

    def fail(self, database: str, error: BaseException) -> None:
        self.failure = error

    def heal(self) -> None:
        self.failure = None
        self.healed += 1

    def sql(self, statement: str) -> list[Any]:
        with psycopg.connect(self.address, autocommit=True) as connection:
            done = connection.execute(statement)
            return [row[0] for row in done.fetchall()] if done.description else []

    def refused(self, statement: str) -> str:
        with pytest.raises(psycopg.Error) as raised:
            self.sql(statement)
        return raised.value.diag.message_primary or ""


def migrated(service: str, schema: str = SEEDED, keep: Sequence[str] = ()) -> Harness:
    harness = Harness(service, schema, keep)
    harness.case()
    return harness


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
    assert harness.sql("insert into categories (name) values ('Other') returning id") == [3]
    assert harness.sql("insert into billing.invoices (vendor_id) values (1) returning id") == [2]
    assert harness.sql("insert into orders (vendor_id) values (1) returning id") == [1]


def test_a_child_written_back_before_its_parent_and_a_tree_come_back() -> None:
    harness = migrated("baseline_order")

    harness.case()

    assert harness.sql("select vendor_id from billing.invoices") == [1]
    assert harness.sql("select parent_id from categories order by id") == [None, 1]


def test_rows_written_back_fire_no_trigger() -> None:
    harness = migrated("baseline_trigger")
    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select what from audit") == ["R2"]

    harness.case()

    assert harness.sql("select what from audit") == []


def test_a_generated_column_is_computed_anew() -> None:
    harness = migrated("baseline_generated")
    harness.sql("update categories set name = 'Renamed' where id = 1")

    harness.case()

    assert harness.sql("select name_length from categories order by id") == [6, 5]


def test_migrations_that_write_no_rows_leave_nothing_aside() -> None:
    harness = migrated("baseline_empty", "create table orders (id serial primary key)")
    harness.sql("insert into orders default values")

    harness.case()

    assert harness.sql("insert into orders default values returning id") == [1]
    assert harness.sql("select count(*) from pg_namespace where nspname = 'specrig_baseline'") == [0]


def test_a_partitioned_table_comes_back_once() -> None:
    harness = migrated(
        "baseline_partitioned",
        "create table events (id bigserial, at date not null, name text not null) partition by range (at);"
        " create table events_2026 partition of events for values from ('2026-01-01') to ('2027-01-01');"
        " insert into events (at, name) values ('2026-03-01', 'seeded');",
    )

    harness.case()

    assert harness.sql("select name from events") == ["seeded"]
    assert harness.sql("insert into events (at, name) values ('2026-04-01', 'next') returning id") == [2]


def test_a_kept_table_is_left_as_it_is() -> None:
    harness = migrated(
        "baseline_keep",
        "create table kept (id serial primary key, name text not null); insert into kept (name) values ('migrated')",
        keep=["kept"],
    )
    harness.sql("insert into kept (name) values ('case')")

    harness.case()

    assert harness.sql("select name from kept order by id") == ["migrated", "case"]
    assert harness.sql("insert into kept (name) values ('next') returning id") == [3]
    assert harness.sql("select count(*) from pg_namespace where nspname = 'specrig_baseline'") == [0]


def test_a_table_the_service_creates_as_it_starts_is_emptied() -> None:
    harness = migrated("baseline_later")
    harness.sql("create table later (id serial primary key, what text)")
    harness.sql("insert into later (what) values ('started')")

    harness.case()

    assert harness.sql("select what from later") == []
    assert harness.sql("insert into later default values returning id") == [1]


def test_rows_to_write_back_without_a_superuser_fail_the_migration() -> None:
    admin = migrated("baseline_admin", "select 1")
    admin.sql("do $$ begin create role specrig_plain login createdb; exception when duplicate_object then null; end $$")
    gate = urlsplit(admin.address)
    harness = Harness(
        "baseline_plain",
        "create table states (id serial primary key, name text not null); insert into states (name) values ('new')",
        address=f"postgresql://specrig_plain@{gate.hostname}:{gate.port}/postgres",
    )

    for _ in range(2):
        with pytest.raises(
            RuntimeError, match="did not migrate: putting back the rows the migrations left needs a superuser"
        ):
            harness.case()


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


def test_a_table_in_a_schema_of_its_own_is_refused_by_its_qualified_name() -> None:
    harness = migrated("refuse_qualified")

    harness.database.refuse("billing.invoices")

    assert harness.refused("insert into billing.invoices (vendor_id) values (1)") == (
        "specrig: insert on invoices is refused"
    )


def test_a_quota_lets_that_many_rows_through() -> None:
    harness = migrated("refuse_after")

    harness.database.refuse_after("states", 1)

    harness.sql("insert into states (name) values ('first')")
    assert harness.refused("insert into states (name) values ('second')") == "specrig: insert on states is refused"
    assert harness.sql("select name from states order by id")[-1] == "first"


def test_a_delete_within_the_quota_deletes() -> None:
    harness = migrated("refuse_after_delete")

    harness.database.refuse_after("categories", 1, Write.DELETE)

    harness.sql("delete from categories where parent_id is not null")
    assert harness.sql("select name from categories") == ["Master"]


def test_a_rolled_back_row_gives_its_place_back() -> None:
    harness = migrated("refuse_rolled_back")
    harness.database.refuse_after("states", 1)

    with psycopg.connect(harness.address) as connection:
        connection.execute("insert into states (name) values ('undone')")
        connection.rollback()

    harness.sql("insert into states (name) values ('kept')")
    assert harness.sql("select name from states order by id")[-1] == "kept"


def test_refusing_again_replaces_the_quota() -> None:
    harness = migrated("refuse_again")
    harness.database.refuse("states")

    harness.database.refuse_after("states", 1)

    harness.sql("insert into states (name) values ('first')")


def test_a_commit_refusal_lets_the_statements_through_and_fails_the_commit() -> None:
    harness = migrated("refuse_commit")
    harness.database.refuse_commit("states")

    with psycopg.connect(harness.address) as connection:
        connection.execute("insert into states (name) values ('extra')")
        with pytest.raises(psycopg.Error, match="specrig: insert on states is refused"):
            connection.commit()

    assert harness.sql("select name from states order by id") == ["new", "active", "closed"]


def test_a_commit_refusal_leaves_a_transaction_that_did_not_write_the_table_alone() -> None:
    harness = migrated("refuse_commit_elsewhere")
    harness.database.refuse_commit("states")

    with psycopg.connect(harness.address) as connection:
        connection.execute("select name from states")
        connection.execute("insert into vendors (state_id, name) values (1, 'R2')")

    assert harness.sql("select name from vendors order by id") == ["R1", "R2"]


def test_heal_lifts_the_refusals_and_the_gate_and_keeps_the_rows() -> None:
    harness = migrated("refuse_heal")
    harness.sql("insert into states (name) values ('extra')")
    harness.database.refuse("vendors")
    harness.database.fail_with()
    with pytest.raises(psycopg.OperationalError):
        harness.sql("select 1")

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


def failing(harness: Harness, error: BaseException) -> tuple[Any, list[BaseException]]:
    engine = create_engine(harness.address.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    seen: list[BaseException] = []
    event.listen(engine, "handle_error", lambda context: seen.append(context.original_exception))
    databases = specrig.fastapi._Databases(None)  # type: ignore[arg-type]
    databases.fail(harness.database._held().database, error)
    try:
        with pytest.raises(sqlalchemy.exc.DBAPIError) as raised, engine.connect() as connection:
            connection.execute(text("select 1"))
    finally:
        databases.heal()
        engine.dispose()
    return raised.value, seen


def test_a_drivers_error_given_reaches_a_sqlalchemy_service_as_sqlalchemys() -> None:
    error = psycopg.OperationalError("the disk is full")

    raised, _ = failing(migrated("fail_sqlalchemy"), error)

    assert isinstance(raised, sqlalchemy.exc.OperationalError)
    assert raised.orig is error


@pytest.mark.skipif(
    tuple(int(part) for part in sqlalchemy.__version__.split(".")[:2]) < (2, 1),
    reason="SQLAlchemy handles an event's error as a driver's from 2.1",
)
def test_sqlalchemy_handles_a_drivers_error_given_as_one_the_driver_raised() -> None:
    error = psycopg.OperationalError("the disk is full")

    _, seen = failing(migrated("fail_sqlalchemy_handled"), error)

    assert seen == [error]


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


def test_a_healed_refusal_does_not_wait_for_the_service_to_start() -> None:
    harness = migrated("refuse_late_heal")
    harness.database.release()
    harness.database.refuse("vendors")

    harness.database.heal()
    harness.database.ready(harness)
    harness.database.started()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")


def test_the_reset_before_a_case_lifts_the_refusals() -> None:
    harness = migrated("refuse_reset")
    harness.database.refuse("vendors")
    harness.database.refuse_commit("states")

    harness.case()

    harness.sql("insert into vendors (state_id, name) values (1, 'R2')")
    assert harness.sql("select count(*) from pg_namespace where nspname = 'specrig_refusal'") == [0]


def test_the_refusals_are_no_table_of_the_service() -> None:
    harness = migrated("refuse_hidden")

    harness.database.refuse("vendors")

    assert harness.sql("select count(*) from specrig_refusal.quota") == [1]
    assert all(schema != "specrig_refusal" for schema, _ in harness.database._held()._tables(()))


def test_only_a_write_can_be_refused() -> None:
    with pytest.raises(ValueError, match="'truncate' is not a write, refuse an insert, update or delete"):
        Postgres("DATABASE_URL").refuse("vendors", "truncate")  # type: ignore[arg-type]


def test_a_table_there_is_none_of_is_no_refusal() -> None:
    harness = migrated("refuse_nowhere")

    with pytest.raises(RuntimeError, match="cannot refuse writes to nowhere"):
        harness.database.refuse("nowhere")
