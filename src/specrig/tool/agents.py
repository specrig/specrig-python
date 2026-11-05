from __future__ import annotations

import re
from functools import cache
from importlib.resources import files

from . import modes
from .service import Area, allows

PARTS = ("header", "contributing", "short", "rule11", "reference")
_HEADING = "## Rule #11 — specification before code"
_TOKEN = "@specrig.md"
_SLOT = re.compile(r"\{\{([a-z-]+)\}\}")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@cache
def _part(name: str) -> str:
    return files("specrig").joinpath("protocol", "agents", f"{name}.md").read_text(encoding="utf-8")


def render(part: str, slots: dict[str, str]) -> str:
    if part not in PARTS:
        raise ValueError(f'no part "{part}" (have: {", ".join(PARTS)})')
    unknown = _unknown(slots)
    if unknown:
        raise ValueError(f"no part has the slots {', '.join(unknown)}")
    missing: set[str] = set()
    out = []
    for line in _part(part).split("\n"):
        alone = _SLOT.fullmatch(line)
        if alone and slots.get(alone.group(1), "") == "":
            if alone.group(1) not in slots:
                missing.add(alone.group(1))
            continue

        def filled(m: re.Match[str]) -> str:
            if m.group(1) not in slots:
                missing.add(m.group(1))
            return slots.get(m.group(1), "")

        out.append(_SLOT.sub(filled, line))
    if missing:
        raise ValueError(f"{part} leaves {', '.join(sorted(missing))} unfilled")
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def _unknown(slots: dict[str, str]) -> list[str]:
    known = {name for part in PARTS for name in _SLOT.findall(_part(part))}
    return sorted(set(slots) - known)


def verdict(text: str | None) -> str:
    if text is None:
        return "absent"
    stale, referenced = _scan(text)
    return "stale" if stale else ("ok" if referenced else "unreferenced")


def references(text: str) -> bool:
    return _scan(text)[1]


def _scan(text: str) -> tuple[bool, bool]:
    fence: str | None = None
    stale = referenced = False
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        stale = stale or line.rstrip() == _HEADING
        referenced = referenced or _stands_alone(line)
    return stale, referenced


def _stands_alone(line: str) -> bool:
    code = _code_spans(line)
    at = line.find(_TOKEN)
    while at != -1:
        end = at + len(_TOKEN)
        before = at == 0 or line[at - 1].isspace()
        after = end == len(line) or line[end].isspace()
        if before and after and not any(start <= at < stop for start, stop in code):
            return True
        at = line.find(_TOKEN, at + 1)
    return False


def _code_spans(line: str) -> list[tuple[int, int]]:
    runs = [(m.start(), m.group(0)) for m in re.finditer(r"`+", line)]
    spans = []
    i = 0
    while i < len(runs):
        for j in range(i + 1, len(runs)):
            if len(runs[j][1]) == len(runs[i][1]):
                spans.append((runs[i][0], runs[j][0] + len(runs[j][1])))
                i = j
                break
        i += 1
    return spans


def mode_rows() -> str:
    return "\n".join(f"| {mode.name} | {allows(mode)} |" for mode in modes.all())


def area_rows(areas: list[Area]) -> str:
    rows = []
    for area in areas:
        paths = _claimed(areas, area)
        if not paths:
            continue
        if area.modes is None:
            open_in = "as the table says"
        elif area.modes == []:
            open_in = "no mode"
        else:
            open_in = ", ".join(area.modes)
        granted = ", ".join(area.grant) if area.grant else "—"
        rows.append(f"| {area.name} | `{'`, `'.join(paths)}` | {open_in} | {granted} |")
    return "\n".join(rows)


def _claimed(areas: list[Area], area: Area) -> list[str]:
    return [
        path
        for path in area.paths
        if not any(other.name < area.name and _names(path) in map(_names, other.paths) for other in areas)
    ]


def _names(pattern: str) -> str:
    names: list[str] = []
    for name in pattern.split("/"):
        if name in ("", "."):
            continue
        if name == ".." and names and names[-1] != "..":
            names.pop()
            continue
        names.append(name)
    return "/".join(names)
