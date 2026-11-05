from datetime import date, timedelta

from pytest_bdd import given, parsers, then, when

from reminders.jobs import send_reminders
from reminders.models import Reminder
from tests.internal.harness import Harness

DAYS = {"yesterday": -1, "today": 0, "tomorrow": 1}


@given(parsers.parse("billing holds an unpaid invoice of {amount:d} for order {order:d}, due {when}"))
def unpaid(harness: Harness, amount: int, order: int, when: str) -> None:
    due = (date.today() + timedelta(days=DAYS[when])).isoformat()
    harness.billing.reply_to(
        "GET", "/invoices", {"status": "unpaid"}, 200, [{"order": order, "amount": amount, "dueOn": due}]
    )


@given("billing accepts reminders")
def accepts(harness: Harness) -> None:
    harness.billing.reply("POST", "/reminders", 202)


@given("the reminders have run")
@when("the reminders run")
def run(harness: Harness) -> None:
    harness.run(send_reminders)


@when("someone runs the reminders by hand")
def by_hand(harness: Harness) -> None:
    assert harness.command("remind") == 0, harness.output


@then(parsers.parse("billing is asked to remind the customer of {amount:d} for order {order:d}"))
def reminded(harness: Harness, amount: int, order: int) -> None:
    asked = harness.billing.requests("POST", "/reminders")
    assert [r.json for r in asked] == [{"order": order, "amount": amount}]


@then(parsers.parse("billing is asked to remind the customer of {amount:d} for order {order:d} once"))
def reminded_once(harness: Harness, amount: int, order: int) -> None:
    reminded(harness, amount, order)


@then("billing is asked to remind nobody")
def nobody(harness: Harness) -> None:
    assert harness.billing.requests("POST", "/reminders") == []


@then(parsers.parse("order {order:d} is noted as reminded of {amount:d}"))
def noted(order: int, amount: int) -> None:
    assert list(Reminder.objects.filter(order=order).values_list("amount", "sent_on")) == [(amount, date.today())]
