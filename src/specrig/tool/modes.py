from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

from .. import _yaml

FORMAT = "modes/1"


@dataclass(frozen=True)
class Mode:
    name: str
    opens: list[str]
    allows: str
    brief: str
    includes: list[str] = field(default_factory=list)
    offered: bool = True


@dataclass(frozen=True)
class Class:
    name: str
    patterns: list[str]


@cache
def _table() -> tuple[list[Mode], list[Class]]:
    data = _yaml.load(files("specrig").joinpath("protocol", "modes.yaml").read_text(encoding="utf-8"))
    if data.get("specrig") != FORMAT:
        raise ValueError(f"modes.yaml is {data.get('specrig', 'unversioned')}, and this tool reads {FORMAT}")
    modes = [
        Mode(m["name"], m["opens"], m["allows"], m["brief"], m.get("includes") or [], m.get("offered", True))
        for m in data["modes"]
    ]
    classes = [Class(c["name"], c.get("patterns") or []) for c in data["classes"]]
    return modes, classes


def all() -> list[Mode]:
    return _table()[0]


def classes() -> list[Class]:
    return _table()[1]


def find(name: str) -> Mode:
    for mode in all():
        if mode.name == name:
            return mode
    have = ", ".join(m.name for m in all())
    raise ValueError(f'unknown mode "{name}" (have: {have})')
