import json
from pathlib import Path

import pytest

from specrig import flows
from specrig.records import Record, Run

FIXTURES = Path(__file__).parent / "fixtures" / "protocol" / "records"


@pytest.mark.parametrize("name", sorted(p.name.removesuffix(".trace.json") for p in FIXTURES.glob("*.trace.json")))
def test_a_trace_comes_out_as_the_record_every_writer_agrees_on(name: str) -> None:
    trace = json.loads((FIXTURES / f"{name}.trace.json").read_text())
    if any("threads" in r or "round" in r for run in trace for r in run["records"]):
        pytest.skip(f"{name} holds threads or rounds, which a Python writer never sees")
    runs = [
        Run(
            run["case"],
            [
                Record(r["seq"], r["dir"], r["kind"], r["name"], r.get("step", ""), r.get("request"), r.get("error"))
                for r in run["records"]
            ],
        )
        for run in trace
    ]
    actual = flows.encode(flows.build(name, runs))

    expected = FIXTURES / f"{name}.flows.yaml"
    assert expected.is_file(), f"{name}.flows.yaml is missing; the record comes out as\n{actual}"
    assert actual == expected.read_text()
