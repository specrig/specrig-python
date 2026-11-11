from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import agents
from .service import LAYOUT, Service


@dataclass(frozen=True)
class Backend:
    name: str
    attribute: str
    origin: str
    setting: str | None = None
    contract: str | None = None
    failure: str | None = None


@dataclass(frozen=True)
class Resource:
    kind: str
    attribute: str
    setting: str
    scheme: str | None = None


@dataclass(frozen=True)
class Plan:
    service: Service
    name: str
    framework: str
    target: str
    backends: list[Backend] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    constants: dict[str, str] = field(default_factory=dict)


def harness(plan: Plan) -> str:
    imports = sorted({*(["Backend"] if plan.backends else []), *(r.kind for r in plan.resources)})
    lines = [f"import specrig.{plan.framework}"]
    if imports:
        lines.append(f"from specrig import {', '.join(imports)}")
    lines += ["", "", f"class Harness(specrig.{plan.framework}.Harness):", f"    service = {_quoted(plan.name)}"]
    lines.append(f"    {'app' if plan.framework == 'fastapi' else 'settings'} = {_quoted(plan.target)}")
    if plan.backends or plan.resources:
        lines.append("")
    for b in plan.backends:
        given = (("setting", b.setting), ("contract", b.contract), ("failure", b.failure))
        named = [_quoted(b.origin)] + [f"{k}={_quoted(v)}" for k, v in given if v is not None]
        lines.append(f"    {b.attribute} = Backend({', '.join(named)})")
    for r in plan.resources:
        named = [_quoted(r.setting)] + ([f"scheme={_quoted(r.scheme)}"] if r.scheme else [])
        lines.append(f"    {r.attribute} = {r.kind}({', '.join(named)})")
    if plan.constants:
        entries = ", ".join(f"{_quoted(k)}: {_quoted(v)}" for k, v in plan.constants.items())
        lines += ["", f"    constants = {{{entries}}}"]
    return "\n".join(lines) + "\n"


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


CASES = 'from pytest_bdd import scenarios\n\nscenarios("../features", "boot.feature")\n'

BOOT = """Feature: The platform

  Scenario: The service boots with what the harness answers
    When the service boots
"""


def config(guard: list[str]) -> str:
    return f"guard: [{', '.join(guard)}]\n"


def specrig_md(service: Service, version: str) -> str:
    platform = "`" + "`, `".join(LAYOUT["platform"]) + "`"
    surface = f"`{service.command('surface')}`"
    writer = f"`{service.command('agents')}`"
    suite = f"`{service.suite()}`"
    slots = {
        "what": "service",
        "writer": writer,
        "version": f"`specrig` {version}",
        "cut": "`tests/internal/harness.py`",
        "validate": suite,
        "boundary": (
            "What a case may stand in for, and what the service is answered with when it boots, are the "
            "platform owner's whether the rule is in force or not: `tests/internal/harness.py` says both, "
            "and a change there changes what every case means."
        ),
        "spec-paths": "`tests/features/`, `tests/steps/`",
        "platform-paths": platform,
        "tasks": "",
        "modes": agents.mode_rows(),
        "platform-files": platform,
        "generated": (
            f"`tests/surface.yaml` and `tests/checks.yaml` are written by {surface}, `specrig.md` by "
            f"{writer}, and the structure of `tests/flows.yaml` by every whole run of the suite, in any "
            "mode, and never by hand; its `description` fields are yours in any mode."
        ),
        "tool": "specrig",
        "config": "`specrig.yaml`",
        "areas": agents.area_rows(service.areas()),
        "cases": "`.feature` files in `tests/features/`, run by pytest-bdd",
        "contracts": (
            "**The contracts a case leans on are named in `tests/internal/harness.py`,** beside the "
            "neighbour each holds. Every answer a step declares, and every body the service sends, is "
            "checked against them: a stand-in cannot answer what its neighbour cannot, and a route, a "
            "status or a body the contract does not have fails the case."
        ),
        "describes": (
            "**A case describes what the service's callers see and what reaches its neighbours** — an "
            "answer, a request to a neighbour — not its classes or its tables.\n\n**A neighbour answers "
            "what the case declared.** A number in a case comes from a fixture, never computed inside a "
            "step. A neighbour's error codes are contract, held by a case.\n\n**What lands during an "
            "operation is a `Given`.** `That invoice gets paid while the move is being priced` is a situation "
            "its reader recognises; which call opens the window is the binding's."
        ),
        "binding": _BINDING,
    }
    return (
        agents.render("header", slots)
        + "\n"
        + agents.render("contributing", slots)
        + "\n## The short version\n\n"
        + agents.render("short", slots)
        + "\n"
        + agents.render("rule11", slots)
        + "\n"
        + _running(service)
    )


def reference() -> str:
    return agents.render("reference", {"what": "service"}).rstrip()


def agents_stub(name: str, parent_rules: bool) -> str:
    parent = (
        "The repository's rules in `../AGENTS.md` hold here too — ownership, the notation for\n"
        "specifications, a neighbour's rules are not reproduced.\n\n"
        if parent_rules
        else ""
    )
    return (
        f"# AGENTS.md\n\nInstructions for AI coding agents working in {name}.\n\n{reference()}\n\n{parent}"
        f"## This service\n\n<!-- What {name} is for, who calls it, which neighbours it calls and what it\n"
        "publishes. Written by the person who owns the platform. -->\n"
    )


_BINDING = """**Bind at the service's edge, as its callers do.** A step sends a request through
`harness.http()`, runs a job with `harness.run()`, or a command with
`harness.command()`. A `Given` writes fixtures through the service's own models and
sessions; a `Then` reads back through them, not out of the answer.

**The harness** is specrig, set up by `tests/internal/harness.py`. A step in
`tests/steps/` takes it as the `harness` fixture, with an attribute for each
neighbour `harness.py` names:

- `harness.<neighbour>.reply(method, path, status, body, headers)` — what the
  neighbour answers; a segment in braces, `/orders/{id}`, takes any value here and in
  every call below, and the exact path wins;
- `harness.<neighbour>.reply_to(method, path, query, status, body)` — the same, for a
  request whose query has these values;
- `harness.<neighbour>.time_out(method, path)` — the neighbour not answering in time;
- `harness.<neighbour>.requests(method, path)` — what reached the neighbour, for a
  `Then`.

A call to a neighbour no step answered fails the case, and so does a request to an
origin `harness.py` does not name — it is refused before it leaves.

**An outage is not a replacement.** `harness.<resource>.fail_with()` takes the
database or the cache away until the next case. A write that fails halfway is not an
outage: `harness.<database>.refuse(table)` makes one table refuse writes,
`refuse_after(table, rows)` lets that many rows through first, and reads keep working,
so a `Then` can check what the failed write left; `refuse_commit(table)` fails only
the commit. `heal()` is the database coming back: it ends the fault and every refusal
and keeps the rows.

**What may be stood in for is given, not chosen.** `tests/internal/` is read-only. A
case that needs another neighbour, or an answer the stand-in cannot give, is a stop:
name the neighbour or the behaviour, and the assertion it would carry.

**Time.** Set fixture dates relative to today — most cases need no clock. A case that
needs a fixed date sets it in its own `Given` with `harness.clock.set()`."""


def _running(service: Service) -> str:
    return f"""## Running

`{service.suite()}` runs the suite with the Python on this machine, as an IDE runs
it, and starts the resources it needs in Docker; `{service.command("surface")}`
rewrites `tests/surface.yaml` and `tests/checks.yaml` from the code. A project that
rewrites the records in CI and then runs `git diff --exit-code` sees a record that
fell behind as a diff.

`tests/flows.yaml` is what the cases make the service send: every `When` step's call is
an input, and what left the service after it is what it sent. Only the `description`
fields are written by hand: they survive a rewrite, and one whose step is gone moves to
`orphaned` and fails the run until it is moved or deleted.
"""
