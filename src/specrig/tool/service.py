from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from .. import _yaml
from . import modes

LAYOUT: dict[str, list[str]] = {
    "generated": ["tests/surface.yaml", "tests/checks.yaml"],
    "platform": ["tests/internal/", "specrig.yaml", "AGENTS.md", "CLAUDE.md"],
    "cases": ["tests/features/**/*.feature"],
    "tests": ["tests/"],
}

GUARD = [".venv/bin/specrig"]


@dataclass(frozen=True)
class Area:
    name: str
    paths: list[str]
    modes: list[str] | None = None
    grant: list[str] | None = None

    @staticmethod
    def of(name: str, data: Any) -> Area:
        if not isinstance(data, dict):
            raise ValueError(f"agents.areas.{name} is not a mapping of paths, modes and grant")
        if not data.get("paths"):
            raise ValueError(f"agents.areas.{name} names no paths")
        return Area(
            name,
            _strings(name, "paths", data["paths"], "paths"),
            _strings(name, "modes", data["modes"], "modes") if "modes" in data else None,
            _strings(name, "grant", data["grant"], "modes") if "grant" in data else None,
        )


def _strings(name: str, key: str, value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"agents.areas.{name}.{key} is not a list of {what}")
    return value


DEFAULTS = [
    Area("deps", ["pyproject.toml", "uv.lock", "poetry.lock", "requirements*.txt"], ["platform"], ["redgreen"]),
    Area("flows", ["tests/flows.yaml"], ["spec", "redgreen", "platform"]),
    Area("steps", ["tests/steps/"], None, ["platform"]),
]


class Service:
    def __init__(self, root: Path) -> None:
        if not root.is_dir():
            raise ValueError(f"{root} is no directory")
        self.root = root.resolve()
        marker = _yaml.read(self.root / "specrig.yaml")
        self.config: dict[str, Any] = marker if isinstance(marker, dict) else {}

    @cached_property
    def pyproject(self) -> dict[str, Any]:
        file = self.root / "pyproject.toml"
        return tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}

    def areas(self) -> list[Area]:
        areas = {area.name: area for area in DEFAULTS}
        declared = (self.config.get("agents") or {}).get("areas") or {}
        if not isinstance(declared, dict):
            raise ValueError("agents.areas is not a mapping of areas by name")
        for name, data in declared.items():
            areas[str(name)] = Area.of(str(name), data)
        for area in areas.values():
            for mode in [*(area.modes or []), *(area.grant or [])]:
                try:
                    modes.find(mode)
                except ValueError as e:
                    raise ValueError(f"agents.areas.{area.name}: {e}") from None
        return list(areas.values())

    def rules(self) -> dict[str, Any]:
        return {
            "specrig": "rules/1",
            "modes": [
                {
                    "name": m.name,
                    "opens": m.opens,
                    **({"includes": m.includes} if m.includes else {}),
                    **({} if m.offered else {"offered": False}),
                    "allows": allows(m),
                    "brief": m.brief,
                }
                for m in modes.all()
            ],
            "classes": [{"name": c.name, "patterns": [*c.patterns, *LAYOUT.get(c.name, [])]} for c in modes.classes()],
            "areas": [
                {
                    "name": a.name,
                    "paths": a.paths,
                    **({} if a.modes is None else {"modes": a.modes}),
                    **({"grant": a.grant} if a.grant else {}),
                }
                for a in self.areas()
            ],
        }

    def dependencies(self) -> set[str]:
        project = self.pyproject.get("project") or {}
        listed = [*project.get("dependencies", [])]
        for group in (project.get("optional-dependencies") or {}).values():
            listed.extend(group)
        for group in (self.pyproject.get("dependency-groups") or {}).values():
            listed.extend(g for g in group if isinstance(g, str))
        poetry = (self.pyproject.get("tool") or {}).get("poetry") or {}
        listed.extend(poetry.get("dependencies") or {})
        for group in (poetry.get("group") or {}).values():
            listed.extend(group.get("dependencies") or {})
        for requirements in sorted(self.root.glob("requirements*.txt")):
            listed.extend(
                line for line in requirements.read_text().splitlines() if line and not line.startswith(("#", "-"))
            )
        return {_normalized(re.split(r"[\s\[<>=!~;@]", d.strip(), maxsplit=1)[0]) for d in listed if d.strip()}

    def framework(self) -> str:
        found = self.dependencies()
        if "django" in found:
            return "django"
        if "fastapi" in found or "starlette" in found:
            return "fastapi"
        raise ValueError(
            f"{self.root} is neither a FastAPI nor a Django service: its dependencies name neither fastapi nor django"
        )

    # The harness names the service in its records; a service with none yet goes by
    # its project's name.
    def name(self) -> str:
        named = self._harness("service")
        if named is not None:
            return named
        project = (self.pyproject.get("project") or {}).get("name")
        poetry = ((self.pyproject.get("tool") or {}).get("poetry") or {}).get("name")
        return str(project or poetry or self.root.name).lower()

    def django_settings(self) -> str:
        named = self._harness("settings")
        if named is not None:
            return named
        for file in [
            self.root / "manage.py",
            *sorted(self.root.glob("*/wsgi.py")),
            *sorted(self.root.glob("*/asgi.py")),
        ]:
            if not file.is_file():
                continue
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setdefault"
                    and len(node.args) == 2
                    and all(isinstance(a, ast.Constant) for a in node.args)
                    and node.args[0].value == "DJANGO_SETTINGS_MODULE"  # type: ignore[attr-defined]
                ):
                    return str(node.args[1].value)  # type: ignore[attr-defined]
        raise ValueError(f"{self.root} names no settings module: manage.py sets no DJANGO_SETTINGS_MODULE")

    def _harness(self, attribute: str) -> str | None:
        harness = self.root / "tests" / "internal" / "harness.py"
        if not harness.is_file():
            return None
        for node in ast.walk(ast.parse(harness.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == attribute for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
        return None

    def clients(self) -> dict[str, str]:
        return dict(((self.config.get("flows") or {}).get("clients")) or {})

    def guard(self) -> list[str]:
        guard = self.config.get("guard")
        return [str(a) for a in guard] if isinstance(guard, list) and guard else list(GUARD)

    # The commands specrig.md names are run the way the plugin runs the tool.
    def command(self, *args: str) -> str:
        return " ".join([*self.guard(), *args])

    def suite(self) -> str:
        guard = self.guard()
        last = guard[-1]
        runner = last[: -len("specrig")] + "pytest" if last.endswith("specrig") else "pytest"
        return " ".join([*guard[:-1], runner, "tests/internal"])


def allows(mode: modes.Mode) -> str:
    return mode.allows.replace("{{cases}}", "`" + "`, `".join(LAYOUT["cases"]) + "`")


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
