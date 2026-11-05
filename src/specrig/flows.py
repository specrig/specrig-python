from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import _yaml
from .records import Run

FORMAT = "flows/1"

_HEADER = (
    "# Generated from the component suite by specrig: every whole run rewrites the\n"
    "# structure; only the description fields are yours.\n"
)


@dataclass
class Branch:
    sends: list[str] | None = None
    refused: str | None = None
    sent: dict[str, list[str]] | None = None
    description: str | None = None
    cases: list[str] = field(default_factory=list)

    def anchor_in(self, input: Input) -> str:
        refused = "" if self.refused is None else f" (refused {self.refused})"
        return f"{input.message} -> {', '.join(self.sends or [])}{refused}"


@dataclass
class Input:
    message: str
    description: str | None = None
    fields: list[str] | None = None
    needs: dict[str, list[str]] | None = None
    branches: list[Branch] = field(default_factory=list)

    def anchor(self) -> str:
        return self.message


@dataclass
class Output:
    message: str
    fields: list[str] | None = None


@dataclass
class Orphan:
    anchor: str
    description: str | None = None
    title: str | None = None


@dataclass
class Doc:
    service: str = ""
    specrig: str = FORMAT
    inputs: list[Input] = field(default_factory=list)
    outputs: list[Output] | None = None
    orphaned: list[Orphan] | None = None


@dataclass
class _Shape:
    every: set[str] | None = None
    any: set[str] = field(default_factory=set)


def build(service: str, runs: Iterable[Run]) -> Doc:
    inputs: dict[str, Input] = {}
    branches: dict[str, dict[str, Branch]] = {}
    fields: dict[str, set[str]] = {}
    outputs: dict[str, set[str]] = {}
    received: dict[int, _Shape] = {}
    sent_fields: dict[int, dict[str, set[str]]] = {}

    for run in sorted(runs, key=lambda r: r.case):
        records = sorted(run.records, key=lambda r: r.seq)
        marked = any(r.step != "" for r in records)

        def acting(r: Any, marked: bool = marked) -> bool:
            return not marked or r.step == "Action"

        last = -1
        sent: dict[int, list[str]] = {}
        carried: dict[int, dict[str, set[str]]] = {}
        for i, r in enumerate(records):
            message = f"{r.kind} {r.name}"
            if r.dir == "in" and acting(r):
                last = i
                sent[i] = []
                carried[i] = {}
                if message not in inputs:
                    inputs[message] = Input(message)
                    branches[message] = {}
                    fields[message] = set()
                _paths(r.request, fields[message])
            elif r.dir == "out":
                _paths(r.request, outputs.setdefault(message, set()))
                if last >= 0 and acting(r):
                    sent[last].append(message)
                    _paths(r.request, carried[last].setdefault(message, set()))
        for i in sorted(sent):
            r = records[i]
            message = f"{r.kind} {r.name}"
            passes = "\n".join(_passes(sent[i]))
            key = f"{passes}\0{r.error or ''}"
            if key not in branches[message]:
                branch = Branch(None if passes == "" else passes.split("\n"), r.error or None)
                branches[message][key] = branch
                received[id(branch)] = _Shape()
                sent_fields[id(branch)] = {}
            branch = branches[message][key]
            if run.case not in branch.cases:
                branch.cases.append(run.case)
            got: set[str] = set()
            _paths(r.request, got)
            shape = received[id(branch)]
            shape.every = set(got) if shape.every is None else shape.every & got
            shape.any |= got
            for to, carried_fields in carried[i].items():
                sent_fields[id(branch)].setdefault(to, set()).update(carried_fields)

    doc = Doc(service)
    for message in sorted(inputs):
        input = inputs[message]
        input.fields = sorted(fields[message]) or None
        answered = [b for b in branches[message].values() if b.refused is None]
        input.needs = _needs(received, answered)
        for branch in branches[message].values():
            branch.cases = sorted(branch.cases)
            kept = {to: sorted(set_) for to, set_ in sorted(sent_fields[id(branch)].items()) if set_}
            branch.sent = kept or None
        input.branches = sorted(branches[message].values(), key=lambda b: b.anchor_in(input))
        doc.inputs.append(input)
    if outputs:
        doc.outputs = [Output(message, sorted(outputs[message]) or None) for message in sorted(outputs)]
    return doc


# A need splits the answered branches exactly, or fixtures naming a unit in most
# cases would make the unit a need of the promo redeemed in some of them.
def _needs(received: dict[int, _Shape], answered: list[Branch]) -> dict[str, list[str]] | None:
    needs: dict[str, list[str]] = {}
    messages = list(dict.fromkeys(m for b in answered for m in b.sends or []))
    for message in messages:
        with_ = [b for b in answered if message in (b.sends or [])]
        without = [b for b in answered if b not in with_]
        if not without:
            continue
        split = set(received[id(with_[0])].every or set())
        for b in with_[1:]:
            split &= received[id(b)].every or set()
        for b in without:
            split -= received[id(b)].any
        if split:
            needs[message] = sorted(split)
    return {m: needs[m] for m in sorted(needs)} or None


def _passes(sends: list[str]) -> list[str]:
    passes = list(sends)
    folded = True
    while folded:
        folded = False
        size = 1
        while size <= len(passes) // 2 and not folded:
            for i in range(len(passes) - 2 * size + 1):
                if passes[i : i + size] == passes[i + size : i + 2 * size]:
                    del passes[i + size : i + 2 * size]
                    folded = True
                    break
            size += 1
    return passes


def _paths(body: Any, into: set[str], prefix: str = "") -> None:
    if isinstance(body, dict):
        for key, child in body.items():
            _paths(child, into, str(key) if prefix == "" else f"{prefix}.{key}")
    elif isinstance(body, list):
        for child in body:
            _paths(child, into, f"{prefix}[]")
    elif body is not None and prefix != "":
        into.add(prefix)


def reconcile(old: Doc | None, fresh: Doc) -> Doc:
    prose: dict[str, str] = {}
    for input in old.inputs if old else []:
        if input.description is not None:
            prose[input.anchor()] = input.description
        for branch in input.branches:
            if branch.description is not None:
                prose[branch.anchor_in(input)] = branch.description
    orphaned: list[Orphan] = []
    for orphan in (old.orphaned or []) if old else []:
        if orphan.anchor in prose:
            orphaned.append(orphan)
        elif orphan.description is not None:
            prose[orphan.anchor] = orphan.description
    used: set[str] = set()
    for input in fresh.inputs:
        if input.anchor() in prose:
            input.description = prose[input.anchor()]
            used.add(input.anchor())
        for branch in input.branches:
            if branch.anchor_in(input) in prose:
                branch.description = prose[branch.anchor_in(input)]
                used.add(branch.anchor_in(input))
    for anchor in sorted(prose):
        if anchor not in used:
            orphaned.append(Orphan(anchor, prose[anchor]))
    fresh.orphaned = orphaned or None
    return fresh


def read(file: Path) -> Doc | None:
    data = _yaml.read(file)
    if not isinstance(data, dict):
        return None
    version = str(data.get("specrig", FORMAT))
    if version != FORMAT:
        raise ValueError(f"{file} is {version}, and this writer reads {FORMAT}")
    doc = Doc(str(data.get("service", "")), version)
    for raw in data.get("inputs") or []:
        input = Input(str(raw["message"]), raw.get("description"), raw.get("fields"), raw.get("needs"))
        for b in raw.get("branches") or []:
            input.branches.append(
                Branch(b.get("sends"), b.get("refused"), b.get("sent"), b.get("description"), b.get("cases") or [])
            )
        doc.inputs.append(input)
    outputs = [Output(str(o["message"]), o.get("fields")) for o in data.get("outputs") or []]
    doc.outputs = outputs or None
    orphaned = [Orphan(str(o["anchor"]), o.get("description"), o.get("title")) for o in data.get("orphaned") or []]
    doc.orphaned = orphaned or None
    return doc


def encode(doc: Doc) -> str:
    return _HEADER + _yaml.emit(
        {
            "specrig": doc.specrig,
            "service": doc.service,
            "inputs": [
                {
                    "message": input.message,
                    "description": input.description,
                    "fields": input.fields,
                    "needs": input.needs,
                    "branches": [
                        {
                            "sends": branch.sends,
                            "sent": branch.sent,
                            "refused": branch.refused,
                            "description": branch.description,
                            "cases": branch.cases,
                        }
                        for branch in input.branches
                    ],
                }
                for input in doc.inputs
            ],
            "outputs": [{"message": o.message, "fields": o.fields} for o in doc.outputs or []],
            "orphaned": [
                {"anchor": o.anchor, "title": o.title, "description": o.description} for o in doc.orphaned or []
            ],
        }
    )


def write(file: Path, service: str, runs: Iterable[Run]) -> int:
    old = read(file)
    fresh = build(service, runs)
    if not fresh.inputs and not (old.inputs if old else []):
        return 0
    merged = reconcile(old, fresh)
    body = encode(merged)
    if not file.is_file() or file.read_text(encoding="utf-8") != body:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
    return len(merged.orphaned or [])
