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
        "test_what_the_service_cached_is_forgotten",
        "test_what_the_service_cached_is_forgotten_again",
    ):
        assert results[name][0] == "passed", f"{name}: {results[name][1]}\n{out}"


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
"""

TAKEN = """import pytest
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
        "test_an_error_given_fails_every_statement",
        "test_the_reminders_fail_while_their_cache_is_down",
    ]
