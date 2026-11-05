from datetime import date

from pytest_bdd import given, parsers, then, when

from shop.db import Order, Session
from shop.jobs import settle_payments
from specrig import Response
from tests.internal.harness import Harness


@given("billing accepts invoices")
def accepts(harness: Harness) -> None:
    harness.billing.reply("POST", "/invoices", 201)


@given(parsers.parse("billing lists order {order:d} paid"))
def listed_paid(harness: Harness, order: int) -> None:
    paid = [{"order": order, "amount": 120, "dueOn": date.today().isoformat()}]
    harness.billing.reply_to("GET", "/invoices", {"status": "paid"}, 200, paid)


@when(parsers.parse("the customer places order {order:d} of {amount:d}"), target_fixture="placed")
def place(harness: Harness, order: int, amount: int) -> Response:
    return harness.http().post("/api/orders", {"id": order, "amount": amount})


@when("the shop settles its payments")
def settle(harness: Harness) -> None:
    harness.run(settle_payments)


@when(parsers.parse("someone cancels order {order:d} by hand"))
def cancel(harness: Harness, order: int) -> None:
    assert harness.command("cancel", str(order)) == 0, harness.output


@then(parsers.parse("order {order:d} of {amount:d} is kept unpaid"))
def kept(placed: Response, order: int, amount: int) -> None:
    assert placed.status == 201, placed.text
    with Session() as session:
        kept = session.get(Order, order)
    assert (kept.amount, kept.paid) == (amount, False)


@then(parsers.parse("billing is asked to invoice order {order:d} for {amount:d}"))
def invoiced(harness: Harness, order: int, amount: int) -> None:
    asked = harness.billing.requests("POST", "/invoices")
    assert [r.json for r in asked] == [{"order": order, "amount": amount}]


@then("billing is asked to invoice nothing")
def nothing_invoiced(harness: Harness) -> None:
    assert harness.billing.requests("POST", "/invoices") == []


@then("they are told the order is invalid")
def invalid(placed: Response) -> None:
    assert placed.status == 422, placed.text


@then(parsers.parse("order {order:d} is gone"))
def gone(order: int) -> None:
    with Session() as session:
        assert session.get(Order, order) is None
