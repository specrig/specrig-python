from pathlib import Path

import pytest

from specrig import _yaml
from specrig.tool import agents, modes
from specrig.tool.service import Area

FIXTURES = Path(__file__).parent / "fixtures" / "protocol" / "agents"


def filled() -> dict[str, str]:
    return {
        "what": "service",
        "writer": "`.venv/bin/specrig agents`",
        "version": "`specrig` 0.1.0",
        "cut": "`tests/internal/harness.py`",
        "validate": "`.venv/bin/pytest tests/internal`",
        "boundary": "What the harness stands in for is the platform's.",
        "spec-paths": "`tests/features/`",
        "platform-paths": "`tests/internal/`",
        "tasks": "",
        "modes": agents.mode_rows(),
        "platform-files": "`tests/internal/`",
        "generated": "",
        "tool": "specrig",
        "config": "`specrig.yaml`",
        "areas": "| flows | `tests/flows.yaml` | spec, redgreen, platform | — |",
        "cases": "`.feature` files in `tests/features/`",
        "contracts": "",
        "describes": "",
        "binding": "Bind at the edge.",
    }


def test_every_part_renders_with_every_slot_filled() -> None:
    for part in agents.PARTS:
        text = agents.render(part, filled())
        assert "{{" not in text, part
        assert "\n\n\n" not in text, part


def test_a_slot_left_out_is_named() -> None:
    slots = filled()
    del slots["binding"]

    with pytest.raises(ValueError, match="rule11 leaves binding unfilled"):
        agents.render("rule11", slots)


def test_a_slot_no_part_has_is_named() -> None:
    with pytest.raises(ValueError, match="no part has the slots harness"):
        agents.render("rule11", {**filled(), "harness": "x"})


def test_an_empty_slot_alone_on_its_line_takes_the_line() -> None:
    assert (
        "contradictory | a test where the defect is, then the fix. None of the cycle below |\n| a **spike**"
        in agents.render("rule11", filled())
    )


@pytest.mark.parametrize("file", sorted(FIXTURES.glob("*.yaml")), ids=lambda p: p.name)
def test_agents_md_is_judged_as_the_protocols_fixtures_judge_it(file: Path) -> None:
    fixture = _yaml.read(file)
    assert fixture["specrig"] == "agents-fixtures/1"
    for case in fixture["cases"]:
        assert agents.verdict(case["agents"]) == case["verdict"], case["name"]


def test_the_mode_table_names_the_cases() -> None:
    rows = agents.mode_rows()

    assert "| spec | `tests/features/**/*.feature` |" in rows
    assert rows.count("\n") == len(modes.all()) - 1


def test_the_area_table_says_how_each_area_opens() -> None:
    assert agents.area_rows(
        [
            Area("deps", ["pyproject.toml"], ["platform"], ["redgreen"]),
            Area("steps", ["tests/steps/"], None, ["platform"]),
            Area("vendored", ["lib/vendored/"], []),
        ]
    ) == (
        "| deps | `pyproject.toml` | platform | redgreen |\n"
        "| steps | `tests/steps/` | as the table says | platform |\n"
        "| vendored | `lib/vendored/` | no mode | — |"
    )


def test_an_area_another_names_alike_and_decides_is_left_out_of_the_table() -> None:
    assert (
        agents.area_rows([Area("contracts", ["api/"], ["spec"]), Area("api", ["./api"], ["platform"])])
        == "| api | `./api` | platform | — |"
    )
