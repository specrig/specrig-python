from pathlib import Path

from specrig import flows
from specrig.records import Record, Run


def opened(case: str, error: str | None = None) -> Run:
    return Run(
        case,
        [
            Record(1, "in", "http", "GET /orders/{id}", "Action", None, error),
            *([] if error else [Record(2, "out", "http", "GET /invoices/{orderId}", "Action")]),
        ],
    )


def test_a_description_written_by_hand_is_kept_by_its_anchor(tmp_path: Path) -> None:
    file = tmp_path / "flows.yaml"
    flows.write(file, "shop", [opened("orders.feature: Seen")])
    file.write_text(
        file.read_text().replace(
            "  - message: http GET /orders/{id}\n",
            "  - message: http GET /orders/{id}\n    description: what a customer opens\n",
        )
    )

    assert flows.write(file, "shop", [opened("orders.feature: Seen")]) == 0
    assert "    description: what a customer opens\n" in file.read_text()


def test_a_description_whose_anchor_is_gone_is_orphaned_and_comes_back_with_it(tmp_path: Path) -> None:
    file = tmp_path / "flows.yaml"
    file.write_text(
        "specrig: flows/1\nservice: shop\ninputs:\n  - message: http GET /orders/{id}\n    branches:\n"
        "      - sends:\n          - http GET /invoices/{orderId}\n        description: asks billing\n"
        '        cases:\n          - "orders.feature: Seen"\n'
    )

    assert flows.write(file, "shop", [opened("orders.feature: Refused", "HTTP 404 order_not_found")]) == 1
    assert (
        "orphaned:\n  - anchor: http GET /orders/{id} -> http GET /invoices/{orderId}\n    description: asks billing\n"
        in file.read_text()
    )

    assert flows.write(file, "shop", [opened("orders.feature: Seen")]) == 0
    assert "orphaned" not in file.read_text()
    assert "        description: asks billing\n" in file.read_text()


def test_a_run_with_no_input_writes_no_record(tmp_path: Path) -> None:
    file = tmp_path / "flows.yaml"

    assert flows.write(file, "shop", [Run("boot.feature: Boots", [])]) == 0
    assert not file.exists()


def test_a_loop_is_one_pass_whatever_the_number_of_items() -> None:
    def reminded(case: str, orders: int) -> Run:
        sent = [Record(1, "in", "command", "SendReminders", "Action")]
        for i in range(orders):
            sent.append(Record(2 + i, "out", "http", "POST /reminders", "Action"))
        return Run(case, sent)

    doc = flows.build("reminders", [reminded("a.feature: One", 1), reminded("a.feature: Three", 3)])

    assert [b.sends for b in doc.inputs[0].branches] == [["http POST /reminders"]]


def test_a_record_of_a_newer_version_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "flows.yaml"
    file.write_text("specrig: flows/2\nservice: shop\n")

    try:
        flows.read(file)
    except ValueError as e:
        assert "flows/2" in str(e)
    else:
        raise AssertionError("a record of flows/2 was read")
