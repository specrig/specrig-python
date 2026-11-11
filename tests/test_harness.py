import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

SAMPLES = Path(__file__).parents[1] / "samples"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="the samples' resources start in Docker")


def sample(name: str, tmp_path: Path) -> Path:
    shutil.copytree(SAMPLES / "docs", tmp_path / "docs")
    shutil.copytree(SAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return tmp_path / name


def write(dir: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (dir / path).write_text(text)


def suite(dir: Path, *args: str, at: str = "tests/internal") -> tuple[int, str, dict[str, tuple[str, str]]]:
    report = dir.parent / "report.xml"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--junitxml={report}", *args, at],
        cwd=dir,
        capture_output=True,
        text=True,
        timeout=600,
    )
    results = {}
    if report.is_file():
        for case in ElementTree.parse(report).iter("testcase"):
            failed = next((e for e in case if e.tag in ("failure", "error")), None)
            outcome = "passed" if len(case) == 0 or failed is None and case.find("skipped") is None else case[0].tag
            results[case.get("name", "")] = (
                outcome,
                "" if failed is None else f"{failed.get('message', '')}\n{failed.text or ''}",
            )
        report.unlink()
    return done.returncode, done.stdout + done.stderr, results


CATCHES = """Feature: What the harness catches

  Scenario: A call no step answered fails the case
    Given order 7 of 120 is placed
    When the customer opens order 7

  Scenario: A request to an origin the harness does not name fails the case
    When the service calls an origin nobody named

  Scenario: A request past the stand-ins fails the case
    When the service calls past the stand-ins

  Scenario: An answer the contract does not allow fails the case
    Given order 7 of 120 is placed
    And billing answers order 7 without its amount
    When the customer opens order 7

  Scenario: A granted class is replaced wherever the service holds it
    Given billing is stood in for by a class of the case's own
    And order 7 of 120 is placed
    When the customer opens order 7
    Then they see 5 to pay

  Scenario: What is not granted is not replaced
    When a case replaces what is not granted

  Scenario: An error given fails every statement
    Given order 7 of 120 is placed
    And the shop's database fails every statement
    When the customer opens order 7
    Then they are told something went wrong

  Scenario: A driver's error given fails every statement as SQLAlchemy's own
    Given order 7 of 120 is placed
    And the shop's database fails with its driver's error
    When the customer opens order 7
    Then they are told to come back later

  Scenario: What the service cached is forgotten
    Given the service has counted a start
    Then it counts one start, and logs it

  Scenario: What the service cached is forgotten again
    Given the service has counted a start
    Then it counts one start, and logs it
"""

COUNTER = """import logging
from functools import cache


@cache
def started() -> list[int]:
    return []


def count() -> None:
    started().append(1)
    logging.getLogger(__name__).warning("counted a start")
"""

CAUGHT = """import urllib.error
import urllib.request

import httpx
import psycopg
import pytest
from pytest_bdd import given, then, when

from specrig import Response
from tests.internal.harness import Harness


class Paid:
    def __init__(self, url: str, timeout: float) -> None:
        pass

    async def invoice(self, order: int) -> dict:
        return {"order": order, "amount": 5, "dueOn": "2026-01-01"}

    async def close(self) -> None:
        pass


@when("the service calls an origin nobody named")
def elsewhere(harness: Harness) -> None:
    def call() -> None:
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://elsewhere.test/x")

    harness.run(call)


@when("the service calls past the stand-ins")
def past(harness: Harness) -> None:
    def call() -> None:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen("http://elsewhere.test/x", timeout=5)

    harness.run(call)


@given("billing answers order 7 without its amount")
def without_amount(harness: Harness) -> None:
    harness.billing.reply("GET", "/invoices/7", 200, {"order": 7, "dueOn": "2026-01-01"})


@given("billing is stood in for by a class of the case's own")
def stood_in(harness: Harness) -> None:
    harness.replace("shop.billing:Billing", Paid)


@when("a case replaces what is not granted")
def not_granted(harness: Harness) -> None:
    with pytest.raises(RuntimeError, match="shop.jobs:settle_payments is not granted: allow in Harness does not name it"):
        harness.replace("shop.jobs:settle_payments", lambda: 0)


@given("the shop's database fails every statement")
def failing(harness: Harness) -> None:
    harness.database.fail_with(RuntimeError("the disk is full"))


@given("the shop's database fails with its driver's error")
def failing_driver(harness: Harness) -> None:
    harness.database.fail_with(psycopg.OperationalError("the disk is full"))


@then("they are told something went wrong")
def went_wrong(opened: Response) -> None:
    assert opened.status == 500, opened.text


@given("the service has counted a start")
def counted(harness: Harness) -> None:
    harness.boot()
    count()


@then("it counts one start, and logs it")
def one_start(harness: Harness) -> None:
    assert started() == [1]
    assert [(logged.level, logged.message) for logged in harness.logs] == [("warning", "counted a start")]
"""


def test_the_harness_fails_a_case_on_what_it_caught_and_lets_a_granted_class_be_replaced(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    harness = dir / "tests/internal/harness.py"
    harness.write_text(harness.read_text() + '\n    allow = ["shop.billing:Billing"]\n')
    write(
        dir,
        {
            "shop/counter.py": COUNTER,
            "tests/features/catches.feature": CATCHES,
            "tests/steps/caught.py": CAUGHT.replace(
                "from specrig import Response\n",
                "from shop.counter import count, started\nfrom specrig import Response\n",
            ),
        },
    )

    code, out, results = suite(dir, "-k", "fails or granted or forgotten")

    def failed(name: str) -> str:
        outcome, message = results[name]
        assert outcome == "failure", f"{name}: {outcome}\n{out}"
        return message

    assert "billing was called where no step declared its answer: GET /invoices/7" in failed(
        "test_a_call_no_step_answered_fails_the_case"
    )
    assert "requests to an origin Harness does not name: GET https://elsewhere.test/x" in failed(
        "test_a_request_to_an_origin_the_harness_does_not_name_fails_the_case"
    )
    assert "GET http://elsewhere.test/x, past the stand-ins" in failed(
        "test_a_request_past_the_standins_fails_the_case"
    )
    assert "GET /invoices/7 answered 200: $ 'amount' is a required property" in failed(
        "test_an_answer_the_contract_does_not_allow_fails_the_case"
    )
    for name in (
        "test_a_granted_class_is_replaced_wherever_the_service_holds_it",
        "test_what_is_not_granted_is_not_replaced",
        "test_an_error_given_fails_every_statement",
        "test_a_drivers_error_given_fails_every_statement_as_sqlalchemys_own",
        "test_what_the_service_cached_is_forgotten",
        "test_what_the_service_cached_is_forgotten_again",
    ):
        assert results[name][0] == "passed", f"{name}: {results[name][1]}\n{out}"


REFUSED = """Feature: Refused writes

  Scenario: A table refusing its writes fails them, and a database that comes back takes them
    Given billing accepts invoices
    And the shop's orders refuse every write
    When the customer places order 9 of 80
    Then they are told something went wrong
    And order 9 is gone
    And the case's own write to the orders is refused
    When the shop's database comes back
    And the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid

  Scenario: A table refusing its commits leaves no order behind
    Given billing accepts invoices
    And the shop's orders refuse every commit
    When the customer places order 9 of 80
    Then they are told something went wrong
    And order 9 is gone
    And billing is asked to invoice nothing

  Scenario: A case starts with no table refusing
    Given billing accepts invoices
    When the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid
"""

REFUSED_STEPS = """import pytest
from pytest_bdd import given, then, when
from sqlalchemy.exc import DBAPIError

from shop.db import Order, Session
from specrig import Response
from tests.internal.harness import Harness


@given("the shop's orders refuse every write")
def refuse(harness: Harness) -> None:
    harness.database.refuse("orders")


@given("the shop's orders refuse every commit")
def refuse_commit(harness: Harness) -> None:
    harness.database.refuse_commit("orders")


@when("the shop's database comes back")
def heal(harness: Harness) -> None:
    harness.database.heal()


@then("they are told something went wrong")
def went_wrong(placed: Response) -> None:
    assert placed.status == 500, placed.text


@then("the case's own write to the orders is refused")
def refused() -> None:
    with pytest.raises(DBAPIError, match="specrig: insert on orders is refused"):
        with Session.begin() as session:
            session.add(Order(id=8, amount=1, paid=False))
"""


def test_a_fastapi_case_has_a_table_refuse_its_writes_and_gets_the_database_back(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    write(dir, {"tests/features/refused.feature": REFUSED, "tests/steps/refused.py": REFUSED_STEPS})

    code, out, results = suite(dir, "-k", "refusing")

    assert code == 0, out
    assert sorted(results) == [
        "test_a_case_starts_with_no_table_refusing",
        "test_a_table_refusing_its_commits_leaves_no_order_behind",
        "test_a_table_refusing_its_writes_fails_them_and_a_database_that_comes_back_takes_them",
    ]


REFUSED_DJANGO = """Feature: Refused writes

  Scenario: A table refusing its writes fails the run, and a database that comes back takes it
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    And the reminders' table refuses every write
    When the reminders run, whatever comes of it
    Then no reminder is noted for order 7
    When the reminders' database comes back
    And the reminders run
    Then order 7 is noted as reminded of 120
"""

REFUSED_DJANGO_STEPS = """import pytest
from pytest_bdd import given, then, when

from reminders.jobs import send_reminders
from reminders.models import Reminder
from tests.internal.harness import Harness


@given("the reminders' table refuses every write")
def refuse(harness: Harness) -> None:
    harness.database.refuse("reminders_reminder")


@when("the reminders run, whatever comes of it")
def run(harness: Harness) -> None:
    with pytest.raises(Exception, match="specrig: insert on reminders_reminder is refused"):
        harness.run(send_reminders)


@when("the reminders' database comes back")
def heal(harness: Harness) -> None:
    harness.database.heal()


@then("no reminder is noted for order 7")
def none() -> None:
    assert Reminder.objects.filter(order=7).count() == 0
"""


def test_a_django_case_has_a_table_refuse_its_writes_and_gets_the_database_back(tmp_path: Path) -> None:
    dir = sample("reminders", tmp_path)
    write(dir, {"tests/features/refused.feature": REFUSED_DJANGO, "tests/steps/refused.py": REFUSED_DJANGO_STEPS})

    code, out, results = suite(dir, "-k", "refusing")

    assert code == 0, out
    assert sorted(results) == ["test_a_table_refusing_its_writes_fails_the_run_and_a_database_that_comes_back_takes_it"]


ON_ANOTHER_DATABASE = """Feature: The shop on another database

  Scenario: A table refusing its writes fails them, and a database that comes back takes them
    Given billing accepts invoices
    And the shop's orders refuse every write
    When the customer places order 9 of 80
    Then they are told what the refusal makes of it
    And order 9 is gone
    And the case's own write to the orders is refused
    When the shop's database comes back
    And the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid

  Scenario: The database going down leaves the shop unable to reach its orders
    Given billing accepts invoices
    And the shop's database is down
    When the customer places order 9 of 80
    Then they are told the database is unavailable
    When the shop's database comes back
    And the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid

  Scenario: A case starts with the order the last case placed gone
    Given billing accepts invoices
    When the customer places order 9 of 80
    Then order 9 of 80 is kept unpaid
"""

ON_ANOTHER_DATABASE_STEPS = """from pytest_bdd import given, then

from specrig import Response
from tests.internal.harness import Harness


@given("the shop's database is down")
def down(harness: Harness) -> None:
    harness.database.fail_with()


@then("they are told what the refusal makes of it")
def refused_answer(placed: Response) -> None:
    assert placed.status == REFUSED_STATUS, placed.text


@then("they are told the database is unavailable")
def unavailable(placed: Response) -> None:
    assert (placed.status, placed.json()["type"]) == (503, "database_unavailable"), placed.text
"""


@pytest.mark.parametrize(
    ("database", "refused_status"),
    [('MySQL("DATABASE_URL", scheme="mysql+pymysql")', 503), ('SQLite("DATABASE_URL")', 500)],
)
def test_a_fastapi_service_runs_its_cases_on_mysql_and_sqlite(
    tmp_path: Path, database: str, refused_status: int
) -> None:
    dir = sample("shop", tmp_path)
    harness = dir / "tests/internal/harness.py"
    kind = database.split("(")[0]
    harness.write_text(
        harness.read_text()
        .replace('Postgres("DATABASE_URL", scheme="postgresql+psycopg")', database)
        .replace("Postgres", kind)
    )
    write(
        dir,
        {
            "tests/features/database.feature": ON_ANOTHER_DATABASE,
            "tests/steps/refused.py": REFUSED_STEPS,
            "tests/steps/database.py": ON_ANOTHER_DATABASE_STEPS.replace("REFUSED_STATUS", str(refused_status)),
        },
    )

    code, out, results = suite(dir)

    assert code == 0, out
    assert all(outcome == "passed" for outcome, _ in results.values()), out
    assert "test_the_database_going_down_leaves_the_shop_unable_to_reach_its_orders" in results


DJANGO_ON_ANOTHER_DATABASE = """Feature: The reminders on another database

  Scenario: A table refusing its writes fails the run, and a database that comes back takes it
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    And the reminders' table refuses every write
    When the reminders run, whatever comes of it
    Then no reminder is noted for order 7
    When the reminders' database comes back
    And the reminders run
    Then order 7 is noted as reminded of 120

  Scenario: The database going down fails the run as Django fails it
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    And the reminders' database is down
    When the reminders run into the database being down
    When the reminders' database comes back
    And the reminders run
    Then order 7 is noted as reminded of 120
"""

DJANGO_ON_ANOTHER_DATABASE_STEPS = """import pytest
from django.db import OperationalError
from pytest_bdd import given, when

from reminders.jobs import send_reminders
from tests.internal.harness import Harness


@given("the reminders' database is down")
def down(harness: Harness) -> None:
    harness.database.fail_with()


@when("the reminders run into the database being down")
def run_down(harness: Harness) -> None:
    with pytest.raises(OperationalError):
        harness.run(send_reminders)
"""

# Django's MySQL backend wants mysqlclient, which builds against MySQL's C client: PyMySQL stands in.
PYMYSQL = 'import pymysql\n\npymysql.version_info = (2, 2, 1, "final", 0)\npymysql.install_as_MySQLdb()\n'


@pytest.mark.parametrize("database", ['MySQL("DATABASE_URL")', 'SQLite("DATABASE_URL")'])
def test_a_django_service_runs_its_cases_on_mysql_and_sqlite(tmp_path: Path, database: str) -> None:
    dir = sample("reminders", tmp_path)
    harness = dir / "tests/internal/harness.py"
    kind = database.split("(")[0]
    harness.write_text(harness.read_text().replace('Postgres("DATABASE_URL")', database).replace("Postgres", kind))
    settings = dir / "config/settings.py"
    if kind == "MySQL":
        text = PYMYSQL + settings.read_text().replace("backends.postgresql", "backends.mysql")
    else:
        text = settings.read_text().replace(
            'database(os.environ["DATABASE_URL"])',
            '{"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["DATABASE_URL"].removeprefix("sqlite:///")}',
        )
    settings.write_text(text)
    write(
        dir,
        {
            "tests/features/database.feature": DJANGO_ON_ANOTHER_DATABASE,
            "tests/steps/refused.py": REFUSED_DJANGO_STEPS,
            "tests/steps/database.py": DJANGO_ON_ANOTHER_DATABASE_STEPS,
        },
    )

    code, out, results = suite(dir)

    assert code == 0, out
    assert all(outcome == "passed" for outcome, _ in results.values()), out
    assert "test_the_database_going_down_fails_the_run_as_django_fails_it" in results


def test_a_narrowed_run_writes_no_record_and_an_ignored_case_does_not_narrow_one(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    (dir / "tests/flows.yaml").unlink()
    write(
        dir,
        {
            "tests/features/later.feature": "Feature: Later\n\n  @ignore\n  Scenario: Not yet\n    When nothing is bound\n"
        },
    )

    code, out, _ = suite(dir, "-k", "cancelled")
    assert code == 0, out
    assert not (dir / "tests/flows.yaml").exists()

    code, out, results = suite(dir)
    assert code == 0, out
    assert "test_not_yet" not in results
    assert "  - message: command cancel\n" in (dir / "tests/flows.yaml").read_text()


CANCELLING = """Feature: Cancelling

  Scenario Outline: An order is cancelled <how>
    Given order 7 of 120 is placed
    When someone cancels order 7 by hand
    Then order 7 is gone

    Examples:
      | how     |
      | by hand |

    @ignore
    Examples:
      | how      |
      | by robot |
"""


def test_an_outline_row_is_a_case_under_its_title_filled_from_the_row(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    write(dir, {"tests/features/cancelling.feature": CANCELLING})

    code, out, _ = suite(dir)
    assert code == 0, out
    assert '"cancelling.feature: An order is cancelled by hand"' in (dir / "tests/flows.yaml").read_text()


ROUTES = """Feature: Routes

  Scenario: A route of an included router is named with the router's prefix
    When someone pings "/api/v2/ping/anna"

  Scenario: A route of a mounted app is named with the mount's prefix
    When someone pings "/sub/ping/anna"

  Scenario: A route of a router included in a mounted app is named with both prefixes
    When someone pings "/sub/v3/ping/anna"
"""

ROUTES_STEPS = """from pytest_bdd import parsers, when


@when(parsers.parse('someone pings "{path}"'))
def pings(harness, path: str) -> None:
    pinged = harness.http().get(path)
    assert pinged.status == 200, pinged.text
"""

ROUTERS = """

from fastapi import APIRouter

router = APIRouter()


@router.get("/ping/{who}")
async def ping(who: str) -> dict:
    return {"who": who}


app.include_router(router, prefix="/api/v2")
sub = FastAPI()
sub.include_router(router)
sub.include_router(router, prefix="/v3")
app.mount("/sub", sub)
"""


def test_a_route_is_named_with_every_prefix_it_sits_under(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    main = dir / "shop/main.py"
    write(
        dir,
        {
            "shop/main.py": main.read_text() + ROUTERS,
            "tests/features/routes.feature": ROUTES,
            "tests/steps/routes.py": ROUTES_STEPS,
        },
    )

    code, out, _ = suite(dir)
    assert code == 0, out
    flows = (dir / "tests/flows.yaml").read_text()
    assert "  - message: http GET /api/v2/ping/{who}\n" in flows
    assert "  - message: http GET /sub/ping/{who}\n" in flows
    assert "  - message: http GET /sub/v3/ping/{who}\n" in flows


SHARED_TITLE = """Feature: Cancelling

  Scenario Outline: An order cancelled by hand is gone
    Given order <order> of 120 is placed
    When someone cancels order <order> by hand
    Then order <order> is gone

    Examples:
      | order |
      | 7     |
      | 8     |
"""


def test_a_run_short_of_one_row_writes_no_record_though_the_rows_share_a_title(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    (dir / "tests/flows.yaml").unlink()
    write(dir, {"tests/features/cancelling.feature": SHARED_TITLE})

    code, out, _ = suite(dir, "-k", "not 8")
    assert code == 0, out
    assert not (dir / "tests/flows.yaml").exists()

    code, out, _ = suite(dir)
    assert code == 0, out
    assert (dir / "tests/flows.yaml").exists()


SEEDED = """Feature: Seeded

  Scenario: The platform's order is there before the service starts
    When the service boots
    Then order 5 of the platform is kept
"""

SEEDED_STEPS = """from pytest_bdd import then

from shop.db import Order, Session


@then("order 5 of the platform is kept")
def kept() -> None:
    with Session() as session:
        assert session.get(Order, 5).amount == 50
"""

SEED = """
    def seed(self) -> None:
        from shop.db import Order, Session

        with Session.begin() as session:
            session.add(Order(id=5, amount=50, paid=False))
"""


def test_the_suite_is_a_run_of_its_own_with_what_the_platform_seeds(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    (dir / "tests/flows.yaml").unlink()
    harness = dir / "tests/internal/harness.py"
    harness.write_text(harness.read_text() + SEED)
    write(
        dir,
        {
            "tests/conftest.py": "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _unit() -> None:\n"
            "    raise RuntimeError(\"the service's own tests' conftest\")\n",
            "tests/test_unit.py": "def test_unit() -> None:\n    pass\n",
            "tests/features/seeded.feature": SEEDED,
            "tests/steps/seeded.py": SEEDED_STEPS,
        },
    )

    code, out, results = suite(dir, "-k", "seeded or platforms_order")
    assert code == 0, out
    assert list(results) == ["test_the_platforms_order_is_there_before_the_service_starts"]

    code, out, results = suite(dir, at=".")
    assert list(results) == ["test_unit"], out
    assert results["test_unit"][0] == "error"
    assert not (dir / "tests/flows.yaml").exists()


def test_a_description_whose_anchor_is_gone_fails_the_run(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    flows = dir / "tests/flows.yaml"
    flows.write_text(
        flows.read_text() + "orphaned:\n  - anchor: http GET /gone\n    description: a route nobody serves\n"
    )

    code, out, _ = suite(dir)

    assert code == 1
    assert "Error: tests/flows.yaml: some descriptions lost their anchor" in out
    assert "  - anchor: http GET /gone\n    description: a route nobody serves\n" in flows.read_text()


def test_a_setting_the_harness_answers_and_the_service_does_not_read_stops_the_run(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    harness = dir / "tests/internal/harness.py"
    harness.write_text(harness.read_text() + '\n    constants = {"SHOP_NAME": "Corner shop"}\n')

    code, out, _ = suite(dir)

    assert code == 4
    assert "Harness answers SHOP_NAME, which shop does not read" in out


def test_a_setting_the_service_overwrites_as_it_imports_stops_the_run(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    settings = dir / "shop/settings.py"
    settings.write_text(
        'import os\n\nos.environ["BILLING_URL"] = "https://billing.example.com"\n' + settings.read_text()
    )

    code, out, results = suite(dir, "-k", "boots")

    assert code == 1
    assert (
        "BILLING_URL changed after Harness answered it"
        in results["test_the_service_boots_with_what_the_harness_answers"][1]
    )


def test_a_setting_the_service_needs_and_nothing_answers_stops_the_run(tmp_path: Path) -> None:
    dir = sample("shop", tmp_path)
    harness = dir / "tests/internal/harness.py"
    harness.write_text(harness.read_text().replace('setting="BILLING_URL", ', ""))

    code, out, _ = suite(dir)

    assert code == 4
    assert "shop reads BILLING_URL, which Harness does not answer: answer it in constants" in out


DJANGO = """Feature: What the harness takes away

  Scenario: An error given fails every statement
    Given order 7 was reminded of 120 today
    And the reminders' database fails every statement
    When someone asks for the reminder of order 7
    Then they are told something went wrong

  Scenario: The reminders fail while their cache is down
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And the reminders' cache is down
    When the reminders run, whatever comes of it
    Then the run failed on ConnectionError

  Scenario: A driver's error given fails every statement as Django's own
    Given billing holds an unpaid invoice of 120 for order 7, due yesterday
    And billing accepts reminders
    And the reminders' database fails with its driver's error
    When the reminders run into Django's own error
"""

TAKEN = """import psycopg
import pytest
from django.db import OperationalError
from pytest_bdd import given, then, when

from reminders.jobs import send_reminders
from specrig import Response
from tests.internal.harness import Harness


@given("the reminders' database fails every statement")
def failing(harness: Harness) -> None:
    harness.database.fail_with(RuntimeError("the disk is full"))


@given("the reminders' cache is down")
def cache_down(harness: Harness) -> None:
    harness.cache.fail_with()


@given("the reminders' database fails with its driver's error")
def failing_driver(harness: Harness) -> None:
    harness.database.fail_with(psycopg.OperationalError("the disk is full"))


@when("the reminders run into Django's own error")
def run_django_error(harness: Harness) -> None:
    with pytest.raises(OperationalError, match="the disk is full"):
        harness.run(send_reminders)


@when("the reminders run, whatever comes of it")
def run(harness: Harness) -> None:
    with pytest.raises(Exception):
        harness.run(send_reminders)


@then("they are told something went wrong")
def went_wrong(shown: Response) -> None:
    assert shown.status == 500, shown.text


@then("the run failed on ConnectionError")
def failed(harness: Harness) -> None:
    assert [r.error for r in harness.trace.records() if r.dir == "in"] == ["ConnectionError"]
"""


def test_a_django_case_takes_the_database_and_the_cache_away(tmp_path: Path) -> None:
    dir = sample("reminders", tmp_path)
    write(dir, {"tests/features/taken.feature": DJANGO, "tests/steps/taken.py": TAKEN})

    code, out, results = suite(dir, "-k", "every_statement or cache_is_down")

    assert code == 0, out
    assert sorted(results) == [
        "test_a_drivers_error_given_fails_every_statement_as_djangos_own",
        "test_an_error_given_fails_every_statement",
        "test_the_reminders_fail_while_their_cache_is_down",
    ]
