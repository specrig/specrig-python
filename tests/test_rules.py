import json
from pathlib import Path

import pytest

from specrig import _yaml
from specrig.tool import cli, modes
from specrig.tool.service import Service

FIXTURES = Path(__file__).parent / "fixtures" / "protocol" / "markers"


def service(tmp_path: Path, marker: str) -> Service:
    (tmp_path / "specrig.yaml").write_text(marker)
    return Service(tmp_path)


@pytest.mark.parametrize("file", sorted(FIXTURES.glob("*.yaml")), ids=lambda p: p.name)
def test_rules_take_and_refuse_the_markers_as_the_protocols_fixtures_do(tmp_path: Path, file: Path) -> None:
    fixture = _yaml.read(file)
    assert fixture["specrig"] == "markers-fixtures/1"
    for case in fixture["cases"]:
        try:
            service(tmp_path, case["marker"]).rules()
            failed = None
        except ValueError as e:
            failed = str(e)
        if "error" in case:
            assert case["error"] in (failed or ""), case["name"]
        else:
            assert failed is None, case["name"]


def test_the_rules_are_the_modes_the_classes_with_the_layout_and_the_areas(tmp_path: Path) -> None:
    rules = service(
        tmp_path, "guard: [.venv/bin/specrig]\nagents:\n  areas:\n    gen:\n      paths: [app/gen/]\n      modes: []\n"
    ).rules()

    assert rules["specrig"] == "rules/1"
    assert [m["name"] for m in rules["modes"]] == [m.name for m in modes.all()]
    assert {m["name"]: m["allows"] for m in rules["modes"]}["spec"] == "`tests/features/**/*.feature`"
    assert rules["classes"] == [
        {"name": "humans", "patterns": ["CONTRIBUTING.md"]},
        {"name": "generated", "patterns": ["specrig.md", "tests/surface.yaml", "tests/checks.yaml"]},
        {"name": "platform", "patterns": ["tests/internal/", "specrig.yaml", "AGENTS.md", "CLAUDE.md"]},
        {"name": "cases", "patterns": ["tests/features/**/*.feature"]},
        {"name": "tests", "patterns": ["tests/"]},
        {"name": "production", "patterns": []},
    ]
    assert rules["areas"] == [
        {
            "name": "deps",
            "paths": ["pyproject.toml", "uv.lock", "poetry.lock", "requirements*.txt"],
            "modes": ["platform"],
            "grant": ["redgreen"],
        },
        {"name": "flows", "paths": ["tests/flows.yaml"], "modes": ["spec", "redgreen", "platform"]},
        {"name": "steps", "paths": ["tests/steps/"], "grant": ["platform"]},
        {"name": "gen", "paths": ["app/gen/"], "modes": []},
    ]


def test_an_area_closed_in_every_mode_is_printed_as_an_empty_list(tmp_path: Path) -> None:
    printed = json.loads(
        cli.rules_of(service(tmp_path, "agents:\n  areas:\n    gen:\n      paths: [app/gen/]\n      modes: []\n"))
    )

    assert printed["areas"][-1]["modes"] == []
    assert "modes" not in printed["areas"][2]


def test_an_area_naming_a_mode_the_table_does_not_have_is_refused_by_its_name(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match=r'agents.areas.events: unknown mode "review" \(have: spec, redgreen, platform, auto\)'
    ):
        service(
            tmp_path, "agents:\n  areas:\n    events:\n      paths: [../shared/events/]\n      grant: [review]\n"
        ).rules()


def test_an_area_with_no_paths_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agents.areas.events names no paths"):
        service(tmp_path, "agents:\n  areas:\n    events:\n      modes: [spec]\n").rules()


def test_rules_print_one_line_of_json_from_the_services_directory(tmp_path: Path) -> None:
    (tmp_path / "specrig.yaml").write_text("guard: [.venv/bin/specrig]\n")

    code, out = cli.run(["rules"], tmp_path)

    assert code == 0
    assert "\n" not in out
    assert json.loads(out)["specrig"] == "rules/1"


def test_a_marker_the_tool_does_not_take_is_an_error_and_exit_1(tmp_path: Path) -> None:
    (tmp_path / "specrig.yaml").write_text("agents:\n  areas:\n    - events\n")

    assert cli.run(["rules"], tmp_path) == (1, "Error: agents.areas is not a mapping of areas by name")


def test_a_command_the_tool_does_not_have_is_named() -> None:
    code, out = cli.run(["guard", "spec", "x"], Path.cwd())

    assert code == 2
    assert out.startswith('Unknown command "guard"')
