import shutil
from collections.abc import Sequence
from typing import Any, Self
from urllib.parse import urlsplit

import psycopg
import pytest

from specrig import Postgres

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
        self.database.provision()

    def case(self) -> None:
        self.database.release()
        self.database.ready(self)

    def databases(self) -> Self:
        return self

    def migrate(self) -> None:
        self.sql(self.schema)

    def bookkeeping(self) -> list[str]:
        return []

    def sql(self, statement: str) -> list[Any]:
        with psycopg.connect(self.address, autocommit=True) as connection:
            done = connection.execute(statement)
            return [row[0] for row in done.fetchall()] if done.description else []


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
