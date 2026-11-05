from datetime import date, datetime

from pytest_bdd import given, parsers, then, when

from reminders.models import Reminder
from specrig import Response
from tests.internal.harness import Harness


@given(parsers.parse("today is {day}"))
def today(harness: Harness, day: str) -> None:
    harness.clock.set(datetime.strptime(day, "%d %B %Y"))


@given(parsers.parse("order {order:d} was reminded of {amount:d} today"))
def reminded(harness: Harness, order: int, amount: int) -> None:
    harness.boot()
    Reminder.objects.create(order=order, amount=amount, sent_on=date.today())


@given("the reminders' database is down")
def database_down(harness: Harness) -> None:
    harness.database.fail_with()


@when(parsers.parse("someone asks for the reminder of order {order:d}"), target_fixture="shown")
def ask(harness: Harness, order: int) -> Response:
    return harness.http().get(f"/reminders/{order}")


@then(parsers.parse("they see a reminder of {amount:d} sent on {day}"))
def seen(shown: Response, amount: int, day: str) -> None:
    assert shown.status == 200, shown.text
    assert shown.json() == {"order": 7, "amount": amount, "sentOn": day}


@then("they are told the order was never reminded of")
def never(shown: Response) -> None:
    assert shown.status == 404, shown.text


@then("they are told to come back later")
def come_back(shown: Response) -> None:
    assert shown.status == 503, shown.text
