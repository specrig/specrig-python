# specrig for Python

A harness for a FastAPI or Django service developed specification first: the
cases are written and approved before the code, and the code is changed until
they pass. A service is whatever its own code builds — a web API, a job a
CronJob runs, a console command — and usually several of them in one
application.

**Status: built.** Everything below runs green on two samples — `shop`, a FastAPI
API over Postgres through SQLAlchemy and Alembic, with a job and a command, and
`reminders`, a Django service over Postgres and Redis with a job and a management
command; the tests run both again on MySQL and on SQLite. [docs/design.md](docs/design.md) has what each part is built from and
why; what every specrig stack agrees on — the records, the modes, the text of
`specrig.md` — is the [specrig protocol](https://github.com/specrig/protocol),
whose modes and `specrig.md` parts are copied into
[src/specrig/protocol/](src/specrig/protocol).

- **The service runs as it ships.** A case drives the application its own code
  builds: FastAPI's app through its lifespan, Django through its own WSGI
  handler, a real database in a container. Only the edges are replaced.
- **It is driven the way production drives it.** A request through the app's own
  stack, a job by its function, a command through the service's own console.
- **Neighbours are stood in for by their contract.** Every origin the service
  calls is named in `tests/internal/harness.py`. Every request through `httpx`,
  `httpx2` or `requests` reaches a stand-in, whoever built the client. A step
  declares what a neighbour answers; the answer and every request the service
  sends are checked against the neighbour's OpenAPI document. A request to an
  origin the harness does not name, a call no step answered, or anything the
  contract does not allow fails the case. A request through another client is
  refused by the harness's proxy before anything leaves.
- **What a case may replace is granted.** Beyond the stand-ins, a case replaces a
  class or a function of the service only if the harness grants it; any other
  replacement fails the step, naming it.
- **Every setting the service reads is answered.** The harness points the
  service's environment variables at the stand-ins and the resources, unsets
  every other one the code reads, and the run stops before it starts when the
  code needs a variable the harness does not answer.
- **Two records in specrig's format.** `tests/surface.yaml` is what the code can
  serve and call, read from the code. `tests/flows.yaml` is what the cases make
  the service send, step by step. `specrig-flows` reads them beside the
  project's other services' records as one system: one binary for every stack,
  from [specrig-go's releases](https://github.com/specrig/specrig-go#a-projects-flows),
  with no Go needed.
- **A protocol for the people and agents who write the cases.** `init` writes
  `specrig.md`, which holds an agent to the order: cases, approval, then code, and
  one line in the service's `AGENTS.md` brings it in; `specrig agents` rewrites it.

## Ways in and out

| way in | a step drives it with | named in the records |
|---|---|---|
| an HTTP route | `harness.http().get(...)` | `http GET /orders/{order_id}` |
| a job | `harness.run(send_reminders)` | `command send_reminders` |
| a command | `harness.command("cancel", "7")` | `command cancel` |

| way out | in a case |
|---|---|
| a neighbour over HTTP | a stand-in, checked by the neighbour's contract |
| a database, a cache | a resource: the real system, in a container |
| anything else — mail, object storage, a vendor's SDK | a granted seam |

## Getting started

In the service's directory, with its virtual environment in `.venv`:

```bash
.venv/bin/pip install "specrig[fastapi,postgres]"
```

or `specrig[django,postgres]` — `mysql` or `sqlite` in place of `postgres` for a
service on those —, then:

```bash
.venv/bin/specrig init --backend billing=BILLING_URL --contract billing=../docs/billing.yaml
```

`init` reads what the service is from its code, through the same pass as
`surface`: the environment variables it reads, the requests it sends, its
dependencies. `--backend` names a neighbour the service calls over HTTP and the
variable that holds its address; a variable the code does not read is refused,
with the list of those it does, and a service that calls out with no `--backend`
is refused, naming the calls. The database the service reads from `DATABASE_URL`
becomes a resource by what says which it is: a Django settings module's `ENGINE`
first, then the server's driver the service depends on — a `Postgres` for psycopg
or asyncpg, a `MySQL` for mysqlclient, PyMySQL, asyncmy or aiomysql —, and a
`SQLite` only where nothing names a server and `.env.example` gives a `sqlite:`
address or the service depends on aiosqlite; with none of these there is no
database resource, and the variable is a constant like any other. The URL's scheme
comes from `.env.example` where it names the dialect chosen, else from the drivers,
an async one first — `postgresql+asyncpg`, `mysql+asyncmy` before `mysql+aiomysql`,
`mysql+pymysql`, `sqlite+aiosqlite`. Redis becomes a `Redis` where it depends on
`redis` and reads a variable that names it. Every other variable the code needs is
a constant the harness answers empty, and a Django service gets a `SECRET_KEY` of
its own.

| option | |
|---|---|
| `--backend <name>[=<setting>]` | an HTTP neighbour and the variable that points at it; repeatable |
| `--contract <name>=<path>` | its OpenAPI document, from the service's directory |
| `--failure <name>=<schema>` | the schema of a 5xx it may answer on any route |
| `--service <name>` | the service's name in its records; the project's name by default |

`init` writes no file that exists, save `specrig.md` and the line an `AGENTS.md`
lacks. It writes:

| file | who changes it |
|---|---|
| `tests/internal/harness.py` | whoever owns the platform: a new origin, a resource, a grant |
| `tests/internal/test_cases.py` | nobody: it binds the cases to pytest-bdd |
| `tests/internal/boot.feature` | nobody: the case that boots the service with no step of its own |
| `tests/features/` | the cases |
| `tests/steps/` | the steps |
| `specrig.yaml`, naming the tool the plugin asks | whoever owns the platform: an area, a client's neighbour |
| `AGENTS.md`, and `CLAUDE.md` linked to it; to an `AGENTS.md` there already, the line that brings `specrig.md` in | whoever owns the platform: what the service is for |
| `specrig.md` | nobody: `specrig agents` rewrites it |

and `tests/surface.yaml` and `tests/checks.yaml`, and adds the harness to the dev
dependencies of `pyproject.toml` — `[dependency-groups] dev`, or Poetry's dev
group. Installing them again is the one step left, and `init` says so.

The package is not published yet. From a checkout of this repository beside the
service's, the service's environment installs it editable:

```bash
.venv/bin/pip install -e "../specrig-python[fastapi,postgres]"
```

`specrig.yaml` names the tool as the plugin runs it, from the service's
directory: `.venv/bin/specrig`, or wherever the environment `init` ran from lies
inside the repository — `../../.venv/bin/specrig` for a workspace that keeps one
environment at its root. A tool outside the repository is no tool the plugin can
run, and `init` says so.

Then:

```bash
.venv/bin/specrig surface
```

```bash
.venv/bin/specrig agents
```

```bash
.venv/bin/pytest tests/internal
```

The first rewrites `tests/surface.yaml` and `tests/checks.yaml` from the code, the
second `specrig.md` from `specrig.yaml`, and the third runs the suite and writes
`tests/flows.yaml`. The suite is a run of its own, named by its path — that
command, or a test of `tests/internal` an IDE runs: a run of other paths, `pytest`
in the service's directory say, collects no case and leaves the service's own tests
as they were. How and where they run — a `Makefile`, `uv run`, a task
runner, CI — is the project's. The `agents` command fails while `AGENTS.md` does
not bring `specrig.md` in, or still carries Rule #11 from before there was one.

They run on the Python the service is developed with, so an IDE runs and debugs
the same suite. The resources start in Docker from the suite's own process,
through the Docker socket: `DOCKER_HOST`, else where Docker Desktop, OrbStack,
Colima or Rancher Desktop put it.

## Writing a case

A case is a scenario in `tests/features/`:

```gherkin
Feature: Reminders

  Scenario: A customer whose invoice is overdue is reminded
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    When the reminders run
    Then billing is asked to remind the customer of 120 for order 7
    And order 7 is noted as reminded of 120
```

A step no module defines fails the case, and pytest-bdd names it. A module in
`tests/steps/` holds steps, and every module there is part of the suite with no
line anywhere else; a step takes the harness as the `harness` fixture:

```python
from datetime import date, timedelta

from pytest_bdd import given, parsers, then, when
from reminders.jobs import send_reminders
from reminders.models import Reminder
from tests.internal.harness import Harness


@given(parsers.parse("billing holds an unpaid invoice of {amount:d} for order {order:d}, due yesterday"))
def unpaid(harness: Harness, amount: int, order: int) -> None:
    due = (date.today() - timedelta(days=1)).isoformat()
    harness.billing.reply_to("GET", "/invoices", {"status": "unpaid"}, 200, [{"order": order, "amount": amount, "dueOn": due}])


@given("billing accepts reminders")
def accepts(harness: Harness) -> None:
    harness.billing.reply("POST", "/reminders", 202)


@when("the reminders run")
def run(harness: Harness) -> None:
    harness.run(send_reminders)


@then(parsers.parse("billing is asked to remind the customer of {amount:d} for order {order:d}"))
def reminded(harness: Harness, amount: int, order: int) -> None:
    asked = harness.billing.requests("POST", "/reminders")
    assert [r.json for r in asked] == [{"order": order, "amount": amount}]


@then(parsers.parse("order {order:d} is noted as reminded of {amount:d}"))
def noted(order: int, amount: int) -> None:
    assert list(Reminder.objects.filter(order=order).values_list("amount", flat=True)) == [amount]
```

A `When` hands what it got to a `Then` the way pytest-bdd does, through
`target_fixture`. A run of the suite alone — `pytest tests/internal` — loads no
`conftest.py` above `tests/internal`: those are the service's own tests', and their
fixtures, autouse ones included, would change the service the cases see.

| | |
|---|---|
| `harness.<backend>.reply(method, path, status, body, headers)` | what the neighbour answers; `headers`, strings to strings, are added to the response the client sees. A segment in braces, `/orders/{id}`, takes any one segment, the exact path wins over a template and the first declared of two templates over the second — in every call here that names a path |
| `harness.<backend>.reply_to(method, path, query, status, body, headers)` | the same, for a request whose query has these values; the reply naming the most of them wins |
| `harness.<backend>.reply_with(method, path, answer, headers)` | the answer worked out from each request: `answer` takes what `requests` returns one of and gives `(status, body)`; a reply naming no query, checked as the others |
| `harness.<backend>.time_out(method, path, query=None)` | the neighbour does not answer in time: the client meets the timeout it would meet, at once; no reply lifts it |
| `harness.<backend>.heal()` | the neighbour answers again: every `time_out` is lifted, the replies stay |
| `harness.<backend>.requests(method, path)` | what reached the neighbour: `method`, `path`, `query` and `json`, its decoded body; a template returns the requests it takes |
| `harness.http()` | a client of the service's own stack: `get`, `post`, `put`, `patch`, `delete`, `send`; a response with `status`, `headers`, `text`, `json()` |
| `harness.run(job, *args, **kwargs)` | a job — see below |
| `harness.command(name, *args)` | a command of the service's console; returns its exit code, and `harness.output` what it wrote |
| `harness.boot()` | the service, booted now: for a `Given` that writes through the service's models |
| `harness.replace(target, instance)` | a granted class or function of the service, replaced before the service boots |
| `harness.logs` | what the service logged, under its real logging configuration |
| `harness.clock` | the service's clock: the wall clock until a step calls `set()`, then `advance()` |
| `harness.<resource>.fail_with(error=None)` | the resource goes down for the service — see Postgres, MySQL and SQLite, and Redis |
| `harness.<database>.refuse(table, *writes)` | every insert, update and delete on the table, or those given, fails in the database itself, whoever sends it; reads go on working — see Postgres |
| `harness.<database>.refuse_after(table, rows, *writes)` | the same after the first `rows` rows |
| `harness.<postgres>.refuse_commit(table, *writes)` | Postgres only: the statements go through, and the commit of a transaction that wrote the table fails |
| `harness.<database>.heal()` | the database comes back: `fail_with` and every refusal end, and the rows stay |
| `harness.application` | FastAPI: the app the case runs |

The service boots at the first step that reaches it, so a `Given` declares and
replaces before anything starts. Each case starts the service anew: FastAPI's
lifespan runs from the boot to the end of the case, what the service's modules
cache with `functools.cache` or `lru_cache` is forgotten, Django's caches are
cleared and its connections closed, and the case's requests come from an address
of its own, so what the service keeps by its caller's address — a rate limit — is
not the last case's. The resources are provisioned once per run and put back as
the migrations left them before every case, before the service starts.

A call no step answered gets a 501 and fails the case, named. After the case,
every request and every answer is checked against the neighbour's contract: an
unknown route or method, an undocumented status, or a body of the wrong shape
fails it. So does a request the service sends past the stand-ins — through
`urllib`, or `aiohttp` reading the proxy from the environment —, which the
harness's proxy refused. A path template in a step changes nothing there: the
check and the records start from the path the request came with.

A step's request returns when the service has answered it and done everything
the request made it do, background tasks included: nothing is in flight between
steps. So a neighbour cannot be held while a step answers another; `time_out`
gives a case the slow neighbour instead.

**`Given` writes through the service's own models**, never through the operation
under test, and **`Then` reads back through them**, not out of the answer: it
catches "answered right, persisted wrong".

## `harness.py`

```python
import specrig.fastapi
from specrig import Backend, Postgres


class Harness(specrig.fastapi.Harness):
    service = "shop"
    app = "shop.main:app"

    billing = Backend("https://billing.shop.test", setting="BILLING_URL", contract="../docs/billing.yaml", failure="Error")
    database = Postgres("DATABASE_URL", scheme="postgresql+psycopg")

    constants = {"SHOP_NAME": "Corner shop"}
    allow = ["shop.fraud:FraudCheck"]
```

A Django service's extends `specrig.django.Harness` and names its settings module
in `settings = "config.settings"` where FastAPI's names `app`; the rest is the same.

| member | |
|---|---|
| a `Backend` | an HTTP neighbour: its origin, the variable that points the service at it, its OpenAPI document, the schema of its 5xx |
| a `Postgres`, `MySQL`, `SQLite` or `Redis` | a real system, and the variable that receives its address |
| `app` | FastAPI: the application, `module:attribute`; a function there builds a new one for every case |
| `settings` | Django: the settings module |
| `console` | FastAPI: the service's console, `module:callable`, where `pyproject.toml` has more than one script |
| `constants` | the other variables the harness answers, as given |
| `allow` | what a case may replace, as `module:name` |
| `seed()` | what production holds before the service starts that the migrations do not write and no case is about — the first administrator, say —, written through the service's own models into the resources just put back, before every start |

A grant is a name the harness resolves when the service boots, so a renamed class
breaks the boot instead of lingering in a list.

## Jobs and commands

Nothing runs by itself in a case: a scheduler that wakes on its own would send, at
a moment no case chose, what no step answered — a failure with a true message and
no cause in the case. So a step runs it.

**A job** is a function or a class of the service, run by `harness.run(job)`: a
function is called with what the step gives it, a class is built and called by
`__call__`, `run`, `execute` or `handle`. A coroutine runs on the loop the
service's lifespan built its clients on. A job that throws is named in the
records by what it threw, and the step sees the exception.

**A command** runs through the service's own console: Django's management commands
by `call_command`, FastAPI's console as `pyproject.toml`'s single script names it
— a function taking the arguments, a Click command or a Typer app. Its exit code
is the step's result, and a command that exits otherwise than with 0 is named
`exit <code>`.

## Settings

A Python service reads its settings from the environment, and the harness sets
the environment before the service's modules are imported — the steps' included,
since a module that reads a setting as it loads has to read the answer: its
answers, and every other variable `tests/checks.yaml` says the code reads unset,
so a developer's own `BILLING_URL` does not send a case to the real billing and
the service sees the default its code ships with.

The run is checked against `tests/checks.yaml`, which `specrig surface` writes from
the code: the variables it needs and those it reads with a fallback. A needed
variable the harness does not answer stops the run before the first case, named;
so does a variable the harness answers that the code does not read. A read with
no fallback is needed — `os.environ["X"]`, a pydantic-settings field with no
default, django-environ's `env("X")` —; `os.environ.get`, `os.getenv` and a field
with a default are optional.

Django is set up once, before the steps import its models, with `testserver`
beside the hosts the service allows, as Django's own test runner does.

A resource takes its own address from its own chain — the address given, then
`<SERVICE>_TEST_POSTGRES` and the like, then a container — so a developer's
database is welcome where the service's settings are not.

## Postgres

```python
database = Postgres("DATABASE_URL", scheme="postgresql+psycopg")
```

The variable receives one URL with the scheme given, `postgresql` by default. The
server is `address=` if given, else `<SERVICE>_TEST_POSTGRES`, else a container
started through the Docker socket and kept between runs — starting postgres costs
seconds, a database a fraction of one. Its role has no password, so the container
is published on loopback only — on every interface where
`TESTCONTAINERS_HOST_OVERRIDE` says the suite runs in a container of its own; a
container an older specrig started is reused as it was published. The database is
the run's own:
`shop_test_<pid>`, created at the first boot and dropped when the run ends. A run
killed before its end leaves its database behind, and the next run drops it: a
name of that shape, marked by specrig over an hour ago, with nobody connected.

The service reaches it through a gate of the run's own on loopback, so a case can
take it away whenever the service connected. The schema is the service's own,
built at the first boot of the run, before the service starts, since a start may
read its database: `alembic upgrade head` where the service has an `alembic.ini`,
in a process of its own as production runs it, else whatever its lifespan
creates; Django's `migrate`. Then the rows the migrations wrote — a Django data
migration's, alembic's `op.bulk_insert`'s — are copied into a `specrig_baseline`
schema, and where each sequence stood is remembered. Before every case, before
the service starts, the database goes back to what the migrations left: every
table there is is truncated but the migration tool's own and those `keep=` names,
and the copied rows are written back — a row a migration wrote is there in every
case, a case's own is gone, and ids go on after the seeded ones. A table the
service creates as it starts has no rows copied, and is emptied. Writing the rows
back turns foreign keys and triggers off for one transaction, which needs a
superuser: the container's role is one, and migrations that write no rows need
none. A row a migration writes needs no `keep=`; a kept table is one no case
resets.

`harness.database.fail_with()` closes the gate: every connection the service holds
is cut and every new one refused, as by a database that is down, and the service
meets its driver's own exception. An error given instead fails every statement
with it — through SQLAlchemy's events, or Django's execute wrappers. An error of
the driver's DB-API reaches the service as the framework raises one the driver
raised: Django's `OperationalError` for psycopg's, SQLAlchemy's for SQLAlchemy —
which wraps it itself from 2.1, and the harness before. With asyncpg the DB-API is
SQLAlchemy's adapter, so asyncpg's own exceptions pass as given. A fault a
step sets before the boot waits for the service to start, so the migration and
the start go through; the next case starts whole.

`harness.database.refuse("orders")` makes Postgres refuse one table's writes —
every insert, update and delete, or only those named, `Write.INSERT`,
`Write.UPDATE`, `Write.DELETE` — with `specrig: insert on orders is refused`. A
trigger refuses, not the service's driver, so every connection meets it, the
case's own included, and a write in `RETURNING` or a CTE as well as a plain one;
reads and the other tables go on working, so a case can check what the failed
write left behind. The table is `orders` or `schema.orders`. `refuse_after("orders",
1)` lets that many rows through first, and a rolled-back row gives its place back.
`refuse_commit("orders")` lets every statement through and fails the commit of a
transaction that wrote the table, for a case about what gets out before the commit.
A refusal set before the boot waits for the service to start, as a fault does.
`heal()` is the database coming back in the middle of a case: it ends `fail_with`
and every refusal and keeps the rows. The reset before every case lifts the
refusals, before it writes the rows back. They live in a schema of their own,
`specrig_refusal`, which the reset's discovery and the `specrig_baseline` copy never
see.

## MySQL and SQLite

```python
database = MySQL("DATABASE_URL", scheme="mysql+pymysql")
```

```python
database = SQLite("DATABASE_URL", scheme="sqlite+aiosqlite")
```

The scheme is `mysql` and `sqlite` by default. Both are Postgres's resource with
what the dialect lacks worked around: the migrations once per run, the rows they
wrote put back before every case, `keep=`, `fail_with`, `refuse`, `refuse_after` and
`heal`. **There is no `refuse_commit`**: MySQL has no deferred constraint at all,
and the only thing that fails an SQLite commit on demand is a deferred foreign
key, which every driver raises as an `IntegrityError` — a case would test the
conflict branch instead of a failed commit.

**What a refusal raises is the dialect's.** A MySQL trigger refuses with
`SIGNAL SQLSTATE '45000'`, which every MySQL driver raises as an
`OperationalError`, the class of a lost connection: a service that answers an
`OperationalError` as a database down answers a refused write the same way, as
`shop` does with a 503. SQLite's `RAISE(ABORT)` is a failed constraint, so the
service meets an `IntegrityError`. Postgres's refusal is an `InternalError`.

### MySQL

The server is `address=`, else `<SERVICE>_TEST_MYSQL`, else a `mysql:8.4` container
started as root with no password and kept between runs, published as Postgres's
is. MySQL 8.0.19 and up: the tests run on 8.4, and the MySQL ones passed once
against 8.0.46. The database is the run's own, behind a gate, and a killed run's
is dropped by the next, as Postgres's. MySQL keeps no comment on a database, so the
mark is the comment of a `specrig_mark` table inside it; the copied rows sit in
`specrig_kept_<n>` tables beside the service's, which the reset never sees; and the
drop kills the connections left on the database first, since there is no
`WITH (FORCE)`. The harness talks to MySQL through PyMySQL, which the `mysql` extra
brings with `cryptography`: a user with a password over a connection without TLS
signs in with an RSA key. The service talks through the driver its URL names.

**A reset touches only what a case changed.** Truncating a table, creating a
trigger and setting a counter are DDL in MySQL. `CHECKSUM TABLE` over every table
in one statement finds the tables a case changed, and only those are emptied by
`DELETE` and written back, in one transaction, on the harness's own connection,
which checks no foreign keys and keeps an id 0 a migration wrote
(`NO_AUTO_VALUE_ON_ZERO`). An insert trigger of a table with rows to write back and
a delete trigger of a table with rows to delete are dropped first and created
again after, in their order and the `sql_mode` they were made in, and a trigger the
migrations made that a case dropped is made again; a counter is set back only where
it moved. Measured on a laptop, six tables:

| per reset | |
|---|---|
| nothing changed | ~3 ms |
| two rows inserted | 21–24 ms, most of them setting the two counters back |
| a row updated in a table with two triggers | 34–39 ms |
| every table truncated and the rows written back | 118–158 ms |
| Postgres, two rows inserted | ~7 ms |

### SQLite

**There is no address and no container.** The service is answered with a file of
the run's own, `sqlite:////…/specrig-…/shop.db` with the scheme given, in a
temporary directory the run removes at its end: nothing is shared between runs or
processes, and nothing is left to sweep. The copied rows are kept in a second file
that only the resetting connection attaches. SQLite cannot switch a trigger off, so
the reset drops every trigger and creates it again inside its transaction, with
foreign keys off on that connection, and puts `sqlite_sequence` back; it takes
under a millisecond. A virtual table — FTS5, R*Tree — and the tables it keeps
behind it are left as they are: their rows are not reset.

Whether the service's own connections check foreign keys is the framework's, as in
production: Django turns them on, SQLAlchemy leaves them off unless the service
turns them on.

`fail_with()` has no gate to close: every statement through SQLAlchemy or Django
fails with `sqlite3.OperationalError("unable to open database file")`, as from a
file that does not open, raised as the framework raises the driver's own.

## Redis

```python
cache = Redis("REDIS_URL")
```

The server is `address=`, else `<SERVICE>_TEST_REDIS`, else a container of the
run's own — starting redis costs less than a second, so nothing is kept — removed
when the run ends. Before every case `FLUSHDB` empties it. `fail_with()` closes
its gate, as Postgres's.

## The records

Both are specrig's format and say so first — `specrig: surface/1`,
`specrig: flows/1` — so that `specrig-flows` finds the service by them and reads
them beside the others; the protocol's `records.md` is what every writer
agrees on, and its fixtures run here in `tests/test_format.py`. Each is rewritten
by what has its source, and nothing compares it on the way: a project that
rewrites them in CI and then runs `git diff --exit-code` sees a record that fell
behind as a diff.

**`tests/surface.yaml`** lists what the code can serve and call;
`specrig surface` writes it from the service's code, read with Python's `ast` and
never imported — every module but those under `tests/` and `scripts/`, the tools
beside the service, and the `conftest.py` files:

| | recognised at |
|---|---|
| serves | FastAPI: `@app.get` and the other verbs, `api_route`, `add_api_route`, `APIRouter(prefix=...)`, `include_router(..., prefix=...)` and `mount`. Django: the `ROOT_URLCONF`'s `path`, `re_path` and `include`, with the methods the view answers — `require_GET`, `require_POST`, `require_safe`, `require_http_methods`, DRF's `api_view`, a class-based view's methods — and `*` for a view that answers any |
| calls | a request through an `httpx` or `httpx2` client or module, or a `requests` session or module |
| clients | the requests above, by the class that sends them, or the module where no class does |

A name is followed through constants, f-strings, concatenation, `%` and
`.format`, attributes set in `__init__` and the callers of the function that
builds it, and a route that is not a constant at the end of that chain is
refused: it is a contract, and a contract has a name. A route keeps its
parameters as written, without converters — `{order_id:int}` and `<int:order>`
are `{order_id}` and `{order}`, a path converter `*` — and a varying segment of a
request is `{}`; a request whose whole address comes from data is `GET external`,
and one whose address starts with what varies — a base URL from the settings — is
named by the path after it.

What a record cannot learn from the code — the neighbour a client calls — goes
into `specrig.yaml` beside it, with the tool `init` names there for the plugin:

```yaml
guard: [.venv/bin/specrig]
flows:
  clients:
    shop.billing.Billing: billing
```

**`tests/flows.yaml`** lists what the cases make the service send. Whatever a
`When` step makes reach the service is an input — a request by the route it
matched, a job by its name, a command by its verb — and what left the service
after it, until the next `When` or `Given`, is what it sent: a request named by
the route of the neighbour's contract that takes it. A refusal is named by its
status and what the service says it is: `HTTP 404 order_not_found` from a
problem's `type`; a job that threw by its exception's class, a command by
`exit <code>`.

Every whole run rewrites it, a failing one included; a run narrowed by a path,
`-k` or `-m` writes nothing, which the harness tells by the scenarios that ran
against the scenarios in `tests/features/`, row by row of an outline's examples,
`@ignore` left out. Only the
`description` fields are written by hand: they survive a rewrite, and one whose
step is gone moves to `orphaned`, comes back with its step, and fails the run
meanwhile.

## The protocol

The `specrig.md` that `init` writes holds the people and agents who write the cases
to three steps, with a stop after each: the cases, then approval by a human, then
the code, changed until every case that was red for its own reason is green.
Every edit is made in a mode only the human switches — spec, redgreen or
platform, and redgreen until one is switched — and an edit the mode does not allow
is a stop. Auto mode, which the human switches on too and nothing offers, runs the
whole cycle without a stop for a human who reviews it all at once.

| mode | may be edited |
|---|---|
| spec | `tests/features/**/*.feature` |
| redgreen | the steps and production code — anything but the cases and the platform's files |
| platform | the platform's files, and production code for a seam |
| auto | `tests/features/**/*.feature`, the steps and production code — anything but the platform's files |

The platform's files are `tests/internal/`, `specrig.yaml`, `AGENTS.md` and
`CLAUDE.md`; `specrig.md`, `tests/surface.yaml` and `tests/checks.yaml` are
generated and open in no mode. The rest of `tests/` — the steps, and the service's
own unit tests beside them — is the suite's. An **area** is a named set of paths,
opened in its own modes or granted by the human on top of one:

| area | paths | open in | granted in |
|---|---|---|---|
| `deps` | `pyproject.toml`, `uv.lock`, `poetry.lock`, `requirements*.txt` | platform | redgreen — a package a step needs is not a platform change |
| `flows` | `tests/flows.yaml` | every mode — the run writes it, a description is written by hand | — |
| `steps` | `tests/steps/` | as the table says | platform — steps that follow a harness change |

`specrig.yaml` declares the service's own under `agents.areas`, and replaces a
default by naming it; a monorepo's shared contract is one:

```yaml
agents:
  areas:
    contract:
      paths: [../../shared/api/]
      modes: [spec, platform]
```

`rules` prints what the tables of `specrig.md` are rendered from — the modes, the
layout and every area's paths — for the service in the working directory, as one
line of JSON, rules/1 of the protocol:

```
.venv/bin/specrig rules
```

The tool is the service's own dev dependency, so the rules move with the service's
version of specrig. In Claude Code the specrig plugin enforces them:

```
/plugin marketplace add specrig/specrig
/plugin install specrig@specrig
```

`/specrig:spec ./path/to/service` switches, `/specrig:allow deps` grants, and the
plugin reads the rules of the tool `specrig.yaml` names once a session and refuses
an edit the mode does not allow before it happens. It runs on the machine Claude
Code runs on, so it needs the service's environment installed there; without it
the edits are let through, and the plugin says so once.

## Limits

- FastAPI 0.100 and up, and any ASGI application a module builds, through the
  `fastapi` harness; Django 4.2 and up through its WSGI handler. Flask has no
  harness.
- The service is imported once per run: what a module builds as it loads — a
  dictionary it fills, a client it opens at import — is the run's, not the case's.
  A service that keeps such state builds it in its lifespan, or caches it with
  `functools.cache`.
- A neighbour is stood in for where `httpx`, `httpx2` or `requests` sends. A
  request through another client reads the proxy from the environment — `urllib`
  does, `aiohttp` with `trust_env` — and is refused, the case failing on it; a
  client that reads no proxy is not caught at all.
- No bus and no queue: RabbitMQ, Celery and Django's tasks are for a later
  release.
- No gRPC either way.
- A `.env` the service reads itself — python-dotenv's `load_dotenv`,
  pydantic-settings' `env_file` — answers what the harness leaves unset, so on a
  machine that has one an optional setting may not be the default the code ships.
  One read over the environment, overwriting what the harness answered, stops
  the run, naming the variables: the cases would reach what the file names.
- The database is one URL in one variable. A Django service that sets its
  `DATABASES` host by host from several variables is answered on the one the
  harness names only.
- `Postgres`, `MySQL`, `SQLite` and `Redis` are the resources there are.
- One service per run: two suites in one `pytest` process are refused, and a run
  split by `pytest-xdist` writes no `tests/flows.yaml`, since no worker sees the
  whole.
- `tests/surface.yaml` reads what the table above names: a request through a
  library it does not, an SDK say, is not in it.

## Development

```bash
make generate
```

```bash
make test
```

The first rewrites the two samples' `tests/surface.yaml`, `tests/checks.yaml` and
`specrig.md` through the tool. The second installs the package and its dev group
into `.venv` with the local Python, 3.11 or later, and runs the tests: the
records, the contract check, the stand-ins, the protocol's record, agents and
marker fixtures, `rules`, `surface` and `init` over the services in
`tests/fixtures/fastapi` and `tests/fixtures/django`, what the harness catches,
run against copies of the samples, then the suites of the two samples in
`samples/`, which rewrite their `tests/flows.yaml`. The resources start in Docker.
`make lint` runs ruff.

```bash
docker rm -f $(docker ps -aq --filter label=specrig=postgres)
```

removes the kept Postgres container, and `label=specrig=mysql` the MySQL one.

```bash
make protocol
```

refreshes the copies of the protocol from a checkout of it beside this one,
`PROTOCOL` to name another; the tests then run against the new copy.

## Releasing

The package is published to PyPI as `specrig` from a tag `v<version>` matching
`pyproject.toml`'s version: `.github/workflows/release.yml` runs `make generate`,
`make lint` and `make test` on every push to `main` and every tag, fails when they
leave a diff, and for a tag builds the package and publishes it through PyPI's
trusted publishing, with no token.

```bash
git tag v0.1.0
```

```bash
git push origin v0.1.0
```

Once, before the first tag: the project `specrig` on PyPI, with a trusted
publisher for this repository's `release.yml` and its `pypi` environment.

## License

[MIT](LICENSE)
