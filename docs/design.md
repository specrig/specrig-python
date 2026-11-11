# Design

What specrig for Python is built from, where it follows its siblings and where it
cannot, what the owner decided, and the order it was built in.

## Where it sits

| | for | how it reaches the service | records |
|---|---|---|---|
| specrig-js | a web app | a browser, Playwright | the app's requests to its backends |
| specrig-dotnet | a .NET service on the Generic Host | the host `Program` builds, caught as it is built | inputs over HTTP, gRPC, events, jobs and commands, and what the service sends on |
| specrig-php | a Laravel or Symfony service | the application its own bootstrap builds, in the suite's process | the .NET kind, without gRPC |
| specrig-python | a FastAPI or Django service | the app its own modules build, in the suite's process | the PHP kind, without a bus |

It is specrig-php's shape on Python's ground. A FastAPI app and a Django project
are what a Laravel application and a Symfony kernel are: the service as it ships,
built by its own code, which their own test tools already drive in a test
process. The harness does the same, and puts the stand-ins in at the seams Python
has: the transports every HTTP client sends through, the environment, and a
loopback gate in front of each resource.

## One import per run

PHP builds the application anew for every case, and .NET the host. Python's
modules are imported once per process, and the service's are no exception:

- **Django cannot do otherwise.** Its app registry is filled once, and a model
  class cannot be registered twice.
- **The steps hold the service's names.** A step imports `Order` and `Session`
  from the service, as a test of the service would; a service imported again for
  every case would leave the steps holding the classes and the engine of the
  first.
- **Settings are read as the modules load.** pydantic-settings' `Settings()` at a
  module's top, Django's `settings.py`: the environment is the harness's before
  the first import, the steps' included, so it is set before pytest loads a
  conftest that may import the service.

So what makes a case's service its own is everything around the import: FastAPI's
lifespan runs per case, the caches the service's modules keep through `functools`
are forgotten, Django's caches are cleared and its connections closed, the
resources go back to what the migrations left, and every case calls from an
address of its own, so a rate limit kept by address in a module is not spent by
the cases before. What a module builds as it loads is the run's — a stated limit,
not a hidden one.

## A start may read the database

PHP's Laravel and Symfony build the application without touching the database, so
the resources are readied after the boot. A FastAPI service's start often does
read it — creates its tables, seeds, refuses to start without an administrator —
so here the order is: migrate once, put the resources back as the migrations left
them, `seed()` what production holds before the start and the migrations do not
write, start the service, then apply the faults a step set before the boot.

Back as the migrations left them, not empty: a schema without the rows its
migrations write — the states an order moves through, the root of a tree — exists
in no environment, a step that writes them again is a second copy of the
migration that drifts from the first, and a `keep` for them carries one case's
changes into the next. Writing them back turns foreign keys and triggers off for
one transaction, which takes a superuser; migrations that write no rows need none.

## One request at a time

A step's request returns when the service has answered it, as in PHP: the harness
serves a FastAPI app from an ASGI server of its own, in a thread with its own
event loop, and the step waits for the whole exchange — background tasks
included, since Starlette runs them before the response is complete. Django
answers through its WSGI handler on the step's own thread. So nothing is in
flight between steps, and `time_out` stands in for a slow neighbour, as in PHP.

## The pieces and what they are built on

| piece | FastAPI | Django |
|---|---|---|
| boot | the service's modules imported once, at the first boot; the app the harness names — `module:attribute`, a function there called for every case; its lifespan from the boot to the end of the case | `django.setup()` once, before the steps import a model; `testserver` beside the hosts the service allows, as Django's test runner does |
| inbound HTTP | an ASGI server of the harness's own over a blocking portal: the request, its body and its background tasks answered whole; named by the route Starlette put in the scope, with a mount's prefix | the project's `WSGI_APPLICATION`, called with an environ the harness builds; named by `resolve()`'s route |
| settings | environment variables, set before the first import: the harness's answers, and every other variable the code reads unset | the same |
| outbound HTTP | `httpx`'s and `httpx2`'s transports and `requests`' adapter answer from the stand-ins, so every client reaches them whoever built it and whenever | the same |
| a request past them | the harness's proxy: `http_proxy`, `https_proxy` and `all_proxy` point at a thread of the run's own that refuses every request and writes it down | the same |
| a job | a function or a class, by `__call__`, `run`, `execute` or `handle`; a coroutine on the portal's loop, where the lifespan built its clients | the same; a coroutine through `async_to_sync` |
| a command | the console `pyproject.toml`'s single script names: a function of the arguments, a Click command, a Typer app | `call_command` |
| clock | `time-machine`, from the step that sets it | the same |
| logs | a handler on the root logger, beside the service's | the same |
| grants | `replace(name, instance)`: the name replaced in every module of the service that holds it, and in FastAPI's `dependency_overrides` | the name replaced in every module that holds it |
| Postgres | `alembic upgrade head` once per run, in a process of its own, and the rows it wrote kept aside; before every case every table but `alembic_version` and `keep` truncated and those rows written back, with the sequences where they stood; a refusal by the gate, an error by SQLAlchemy's `before_cursor_execute`, a table's writes by triggers in a schema of their own | `migrate` once per run, and the rows it wrote kept aside; every table but Django's bookkeeping and `keep` truncated and those rows written back; an error by the connections' execute wrappers; a table's writes by the same triggers |
| Redis | `FLUSHDB` before every case; a refusal by the gate | the same |
| steps | pytest-bdd: a plugin of `pytest11` marks Given, When and Then, fails a case on what the harness found, and writes `tests/flows.yaml` after a whole run | |
| `tests/surface.yaml` | Python's `ast` over the service's modules: routes from FastAPI's decorators and routers | routes from the `ROOT_URLCONF` and the views' methods |
| containers | the Docker Engine API over its socket, through `http.client`, from the suite's own process: no Docker CLI | |

## The format is not Python's to change

A flow map joins services by the names in their records. The Python writer
writes them as the protocol's `records.md` says, and runs the protocol's record
fixtures, copied into `tests/fixtures/protocol/records/`, in `tests/test_format.py`.
Its YAML is written by hand, as PHP's is, to match Go's byte for byte.

| what | name | Python writes it from |
|---|---|---|
| an HTTP route served | `GET /orders/{order_id}` | Starlette's route path with converters dropped, a `path` converter `*`; Django's route with `<int:order>` as `{order}` |
| an HTTP request sent | `GET /invoices/{orderId}` in `flows`, `GET /invoices/{}` in `surface` | the neighbour's contract template; the code through `ast` |
| a job | `send_reminders` | the function's or the class's name |
| a command | `cancel`, `remind` | the verb the step gave |
| an HTTP refusal | `HTTP 404 order_not_found` | status and the body's `type`, dropped when it is a URI |
| a job or command that failed | `ConnectionError`, `exit 1` | the exception's class; a non-zero exit code, as specrig-dotnet writes them |

## Decisions taken, and why

**pytest-bdd.** Python's Gherkin runners are behave and pytest-bdd; with
pytest-bdd the cases are pytest tests, so an IDE runs and debugs them as any
other, the harness reaches the steps as a fixture, and the plugin is pytest's own
kind. `tests/internal/test_cases.py` binds the features, which pytest-bdd wants
in a test module; every module of `tests/steps/` is registered as a plugin, so a
new one needs no line anywhere, as PHP's suite generator does.

**An ASGI server of the harness's own.** Starlette's `TestClient` is built on
`httpx`, and moves to `httpx2`: a harness on it would depend on the client the
service may itself use, and patch the transports under its own feet. The server
is a blocking portal, the lifespan protocol and one exchange per request, and
serves any ASGI application a module builds.

**Transports, not clients.** A Python service builds its clients where it likes —
at import, in a lifespan, inside a job — so the stand-ins sit where every client
sends: `httpx`'s `HTTPTransport` and `AsyncHTTPTransport`, the same in `httpx2`,
and `requests`' `HTTPAdapter`. Every other client meets the proxy.

**A template names what a step answers, not what the service sent.** As in
specrig-js, a path segment in braces takes any one segment, the exact path wins,
and of two templates the first declared; `reply`, `reply_to`, `reply_with`,
`time_out` and `requests` all take one. The contract check and the trace go on naming a request
by the path it came with, so a template that matches too much is not hidden by
the contract it is checked against. A reply's headers are added to the response
as the transports hand it to the client, so `httpx`, `httpx2` and `requests` see
them alike.

**A gate, not an address.** PHP points a refused connection at a port where
nothing listens, which works because the application is built anew for every
case. Here the engine was built at import, with the address it was given, so the
address is a gate on loopback that the case opens and closes: a refusal reaches
every connection the service holds and every one it makes, whatever its driver.

**Triggers, not hooks, for a refused table.** Ported from gorib's `sqltest`: a
trigger refuses in Postgres itself, so every connection meets it — the service's,
a job's, the case's own — and the service meets the driver's own exception, where
a hook in SQLAlchemy or Django reaches only the clients that go through it. The
triggers, and the quota that lets `refuse_after`'s rows through, live in a
`specrig_refusal` schema, outside the tables the reset truncates and the baseline
copies, and the reset drops it first. A refusal a step sets before the boot waits
for the start, since the reset would lift it and the schema may not exist yet.

**Alembic in a process of its own.** Alembic's `env.py` configures logging as it
runs, disabling the loggers the service already made; production runs the
migration as a job of its own, and so does the harness.

**The suite runs on the developer's Python.** As every specrig stack runs its
native tool: a Python developer has the service's environment, and runs and
debugs the cases from an IDE. Only what the service depends on runs in Docker. The
modes are the other reason: the plugin runs `.venv/bin/specrig rules` on the host.

**`specrig.yaml` names the environment's tool.** `[.venv/bin/specrig]`, run from
the service's directory, is the service's own installed tool and never one
fetched; `init` writes the path of the environment it ran from where that lies in
the repository, so a workspace with one environment at its root is named as it
is. The commands `specrig.md` names follow the same path.

**No `.env` is read for the service.** Where the service reads one itself —
python-dotenv, pydantic-settings' `env_file` —, what it holds answers what the
harness leaves unset: a limit the harness does not hide by patching those
libraries.

## The owner's calls

1. **Python and the frameworks.** Python 3.11 and up; FastAPI 0.90 and up, and any
   ASGI app a module builds; Django 4.2 and up. Not the newest, and not what nobody
   runs any more.
2. **pytest-bdd.** 8.1 and up, and pytest 7.4 and up.
3. **No bus yet.** RabbitMQ, Celery and Django's tasks are a later release.
4. **gRPC.** Neither way.
5. **Enforcement in Claude Code.** As in .NET and PHP: `rules` prints the
   service's rules, and the plugin checks every edit against them.

## Order, and where it stands

| phase | delivers | stands |
|---|---|---|
| 0 | the records and the fixtures | built |
| 1 | boot, settings, HTTP stand-ins and the contract check, the pytest-bdd binding, Postgres, logs, the clock; HTTP, jobs and commands as ways in; `tests/flows.yaml` | built |
| 2 | `tests/surface.yaml` and `tests/checks.yaml`, `init`, `rules`, `specrig.md` | built |
| 3 | Redis, grants, `fail_with`, a refused table and `heal`, reply headers and path templates | built |
| 4 | a bus and a queue | not started |
