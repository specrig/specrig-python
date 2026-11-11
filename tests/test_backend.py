import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from specrig import Backend, standins
from specrig.http import Request, Unreachable, exchange, route
from specrig.proxy import _named
from specrig.records import Trace
from specrig.standins import StandIns

DOCS = Path(__file__).parents[1] / "samples" / "docs"
INVOICE = {"order": 7, "amount": 1, "dueOn": "2026-01-01"}


def billing() -> tuple[Backend, Trace]:
    trace = Trace()
    backend = Backend("https://billing.shop.test", setting="BILLING_URL", contract="billing.yaml", failure="Error")
    backend.attach("billing", trace, DOCS)
    return backend, trace


def get(backend: Backend, path: str) -> int:
    return backend.answer(Request("GET", f"https://billing.shop.test{path}")).status


@pytest.fixture
def stood_in() -> Iterator[Backend]:
    backend, _ = billing()
    standins.serve(StandIns([backend]))
    yield backend
    standins.serve(None)


def test_the_reply_naming_the_most_of_the_query_wins() -> None:
    backend, _ = billing()
    backend.reply("GET", "/invoices", 200, [])
    backend.reply_to("GET", "/invoices", {"status": "paid"}, 200, [{"order": 7, "amount": 1, "dueOn": "2026-01-01"}])

    assert backend.answer(Request("GET", "https://billing.shop.test/invoices?status=paid")).json() == [
        {"order": 7, "amount": 1, "dueOn": "2026-01-01"}
    ]
    assert backend.answer(Request("GET", "https://billing.shop.test/invoices?status=unpaid")).json() == []
    assert backend.failures() == []


def test_a_call_no_step_answered_gets_a_501_and_fails_the_case() -> None:
    backend, _ = billing()

    assert get(backend, "/invoices/7") == 501
    assert backend.failures() == ["billing was called where no step declared its answer: GET /invoices/7"]


def test_a_neighbour_that_does_not_answer_in_time_is_unreachable() -> None:
    backend, _ = billing()
    backend.time_out("GET", "/invoices/7")

    with pytest.raises(Unreachable) as raised:
        get(backend, "/invoices/7")
    assert raised.value.timed_out


def test_what_the_contract_does_not_allow_fails_the_case() -> None:
    backend, _ = billing()
    backend.reply("GET", "/invoices/7", 200, {"order": 7})
    backend.reply("GET", "/payments", 200, {})
    backend.reply("GET", "/invoices/8", 503, {"code": "down"})

    get(backend, "/invoices/7")
    get(backend, "/payments")
    get(backend, "/invoices/8")

    [failure] = backend.failures()
    assert failure.startswith("what billing's contract, billing.yaml, does not allow: ")
    assert "GET /invoices/7 answered 200: $ 'amount' is a required property" in failure
    assert "GET /payments: billing serves no such route" in failure
    assert "GET /invoices/8 answered 503: $ 'message' is a required property" in failure


def test_a_request_is_named_by_the_route_of_the_neighbours_contract() -> None:
    backend, trace = billing()
    backend.reply("GET", "/invoices/7", 200, {"order": 7, "amount": 1, "dueOn": "2026-01-01"})

    get(backend, "/invoices/7")

    assert [r.name for r in trace.records()] == ["GET /invoices/{orderId}"]


@pytest.mark.parametrize("library", ["httpx", "httpx2", "requests"])
def test_the_headers_of_a_reply_reach_the_client_that_asked(stood_in: Backend, library: str) -> None:
    stood_in.reply("GET", "/invoices/7", 200, INVOICE, {"Retry-After": "30", "X-Trace": "abc"})

    answered = importlib.import_module(library).get("https://billing.shop.test/invoices/7")

    assert (answered.headers["retry-after"], answered.headers["x-trace"]) == ("30", "abc")
    assert answered.headers["content-type"] == "application/json"
    assert answered.json() == INVOICE


@pytest.mark.parametrize("library", ["httpx", "httpx2", "requests"])
def test_a_reply_to_a_query_and_one_without_a_body_carry_their_headers_too(stood_in: Backend, library: str) -> None:
    stood_in.reply_to("GET", "/invoices", {"status": "paid"}, 200, [], {"X-Total-Count": "0"})
    stood_in.reply("POST", "/reminders", 204, headers={"Location": "/reminders/1"})
    client = importlib.import_module(library)

    listed = client.get("https://billing.shop.test/invoices?status=paid")
    accepted = client.post("https://billing.shop.test/reminders", json={"order": 7, "amount": 1})

    assert listed.headers["x-total-count"] == "0"
    assert (accepted.status_code, accepted.headers["location"], accepted.content) == (204, "/reminders/1", b"")


def test_a_header_given_replaces_the_default_one_whatever_its_case() -> None:
    backend, _ = billing()
    backend.reply(
        "GET", "/invoices/8", 404, {"code": "gone", "message": "no"}, {"content-type": "application/problem+json"}
    )

    answered = backend.answer(Request("GET", "https://billing.shop.test/invoices/8"))

    assert dict(answered.headers) == {"content-type": "application/problem+json"}


def test_a_segment_in_braces_takes_any_one_segment() -> None:
    backend, _ = billing()
    backend.reply("GET", "/invoices/{order}", 200, INVOICE)

    assert get(backend, "/invoices/7") == 200
    assert get(backend, "/invoices/8") == 200
    assert get(backend, "/invoices/7/lines") == 501
    assert get(backend, "/invoices") == 501
    assert backend.failures()[0] == (
        "billing was called where no step declared its answer: GET /invoices/7/lines, GET /invoices"
    )


def test_a_template_answers_its_own_method_only() -> None:
    backend, _ = billing()
    backend.reply("POST", "/invoices/{order}", 201)

    assert get(backend, "/invoices/7") == 501


def test_the_exact_path_wins_over_a_template_and_of_templates_the_first_declared() -> None:
    backend, _ = billing()
    backend.reply("GET", "/invoices/{order}", 200, {**INVOICE, "amount": 2})
    backend.reply("GET", "/invoices/{id}", 200, {**INVOICE, "amount": 3})
    backend.reply("GET", "/invoices/7", 200, INVOICE)

    def amount(path: str) -> int:
        return backend.answer(Request("GET", f"https://billing.shop.test{path}")).json()["amount"]

    assert amount("/invoices/7") == 1
    assert amount("/invoices/8") == 2


def test_the_requests_a_template_takes_are_the_ones_returned() -> None:
    backend, _ = billing()
    backend.reply("GET", "/invoices/{order}", 200, INVOICE)
    backend.reply("GET", "/payments", 200, {})
    for path in ("/invoices/7", "/invoices/8", "/payments", "/invoices/7/lines"):
        get(backend, path)

    assert [r.path for r in backend.requests("GET", "/invoices/{order}")] == ["/invoices/7", "/invoices/8"]
    assert [r.path for r in backend.requests("get", "/invoices/7")] == ["/invoices/7"]
    assert backend.requests("POST", "/invoices/{order}") == []


def test_a_neighbour_that_does_not_answer_in_time_is_unreachable_by_template_too() -> None:
    backend, _ = billing()
    backend.time_out("GET", "/invoices/{order}")
    backend.reply("GET", "/payments", 200, {})

    with pytest.raises(Unreachable) as raised:
        get(backend, "/invoices/8")
    assert raised.value.timed_out
    assert get(backend, "/payments") == 200


def test_the_contract_check_and_the_trace_name_a_request_by_its_real_path() -> None:
    backend, trace = billing()
    backend.reply("GET", "/invoices/{order}", 200, {"order": 7})
    backend.reply("GET", "/payments/{id}", 200, {})

    get(backend, "/invoices/7")
    get(backend, "/payments/7")

    [failure] = backend.failures()
    assert "GET /invoices/7 answered 200: $ 'amount' is a required property" in failure
    assert "GET /payments/7: billing serves no such route" in failure
    assert [r.name for r in trace.records()] == ["GET /invoices/{orderId}", "GET /payments/7"]


@pytest.mark.parametrize(
    ("template", "named"),
    [
        ("/orders/{order_id}", "/orders/{order_id}"),
        ("/orders/{id:int}/", "/orders/{id}"),
        ("/files/{rest:path}", "/files/*"),
    ],
)
def test_a_route_is_named_as_the_go_record_names_it(template: str, named: str) -> None:
    assert route(template, lambda name, kind: kind == "path") == named


@pytest.mark.parametrize(
    ("body", "refused"),
    [
        (b'{"type": "order_not_found"}', "HTTP 404 order_not_found"),
        (b'{"type": "https://tools.ietf.org/html/rfc9110#section-15.5.5"}', "HTTP 404"),
        (b"not found", "HTTP 404"),
    ],
)
def test_a_refusal_is_named_by_its_status_and_the_type_the_service_gives_it(body: bytes, refused: str) -> None:
    from specrig.http import Response

    trace = Trace()
    exchange(trace, Request("GET", "http://testserver/orders/8"), lambda r: (Response(404, {}, body), "/orders/{id}"))

    assert trace.records()[0].error == refused


def test_a_tunnel_the_proxy_refused_is_named_by_its_origin() -> None:
    assert _named("CONNECT billing.example.com:443 HTTP/1.1") == "https://billing.example.com"
    assert _named("GET http://billing.example.com/x HTTP/1.1") == "GET http://billing.example.com/x"
