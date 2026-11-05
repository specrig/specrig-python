from datetime import date, timedelta

from pytest_bdd import given, parsers, then, when

from shop.db import Order, Session
from specrig import Response
from tests.internal.harness import Harness


@given(parsers.parse("order {order:d} of {amount:d} is placed"))
def placed(harness: Harness, order: int, amount: int) -> None:
    harness.boot()
    with Session.begin() as session:
        session.add(Order(id=order, amount=amount, paid=False))


@given(parsers.parse("billing holds an invoice of {amount:d} for order {order:d}"))
def invoice(harness: Harness, amount: int, order: int) -> None:
    due = (date.today() + timedelta(days=14)).isoformat()
    harness.billing.reply("GET", f"/invoices/{order}", 200, {"order": order, "amount": amount, "dueOn": due})


@given("the shop's database is down")
def database_down(harness: Harness) -> None:
    harness.database.fail_with()


@given("billing does not answer in time")
def billing_slow(harness: Harness) -> None:
    harness.billing.time_out("GET", "/invoices/7")


@when(parsers.parse("the customer opens order {order:d}"), target_fixture="opened")
def open_order(harness: Harness, order: int) -> Response:
    return harness.http().get(f"/api/orders/{order}")


@then(parsers.parse("they see {amount:d} to pay"))
def to_pay(opened: Response, amount: int) -> None:
    assert opened.status == 200, opened.text
    assert opened.json()["toPay"] == amount


@then("they are told there is no such order")
def no_such_order(opened: Response) -> None:
    assert opened.status == 404, opened.text


@then("they are told to come back later")
def come_back(opened: Response) -> None:
    assert opened.status == 503, opened.text
