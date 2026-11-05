from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .. import surface as records
from . import agents
from .service import Service

USAGE = """specrig init [dir] --backend <name>[=<setting>] [--contract <name>=<path>] [--failure <name>=<schema>] [--service <name>]
specrig surface [dir] [--service <name>]
specrig agents [dir]
specrig rules

init lays the harness out beside a service; surface writes tests/surface.yaml and
tests/checks.yaml from the service's code; agents writes specrig.md, Rule #11 for
the service, and fails while AGENTS.md does not bring it in; rules prints the
service's Rule #11 rules, rules/1, in its directory, for the plugin that enforces
the modes."""

WRITTEN = {"created": "+ specrig.md", "rewritten": "~ specrig.md", "current": "= specrig.md"}


def main(argv: list[str] | None = None) -> int:
    code, out = run(sys.argv[1:] if argv is None else argv, Path.cwd())
    if out:
        print(out.rstrip(), file=sys.stdout if code == 0 else sys.stderr)
    return code


def run(args: list[str], cwd: Path) -> tuple[int, str]:
    command = args[0] if args else ""
    try:
        if command == "init":
            from .init import init

            return init(args[1:], cwd)
        if command == "surface":
            return surface(args[1:], cwd)
        if command == "agents":
            return agents_md(args[1:], cwd)
        if command == "rules":
            return (0, rules_of(Service(cwd))) if len(args) == 1 else (2, USAGE)
        if command in ("", "-h", "--help", "help"):
            return 2, USAGE
        return 2, f'Unknown command "{command}"\n{USAGE}'
    except (ValueError, RuntimeError, OSError) as e:
        return 1, f"Error: {e}"


def rules_of(service: Service) -> str:
    return json.dumps(service.rules(), ensure_ascii=False, separators=(",", ":"))


def surface(args: list[str], cwd: Path) -> tuple[int, str]:
    from ..code import read

    positional, flags = parse(args, ["--service"])
    service = Service(resolve(positional[0] if positional else ".", cwd))
    found = read(service, flags.get("--service", [service.name()])[0])
    return 0, "\n".join(rewrite(service, found))


def agents_md(args: list[str], cwd: Path) -> tuple[int, str]:
    positional, _ = parse(args, [])
    service = Service(resolve(positional[0] if positional else ".", cwd))
    file = service.root / "AGENTS.md"
    if not file.is_file():
        raise ValueError(
            f"there is no AGENTS.md here: `{service.command('init')}` writes one that brings specrig.md in"
        )
    written = WRITTEN[write_specrig_md(service)]
    verdict = agents.verdict(file.read_text(encoding="utf-8"))
    if verdict == "stale":
        return 1, (
            f"{written}\nError: AGENTS.md still carries Rule #11, which specrig.md holds now: keep the "
            "service's own sections in it and delete the rest"
        )
    if verdict == "unreferenced":
        from .templates import reference

        return 1, (
            f"{written}\nError: AGENTS.md does not bring specrig.md in: add the line\n\n{reference()}\n\n"
            "where an agent reads it before the first edit"
        )
    return 0, written


def write_specrig_md(service: Service) -> str:
    from .templates import specrig_md

    body = specrig_md(service, tool_version())
    file = service.root / "specrig.md"
    before = file.read_text(encoding="utf-8") if file.is_file() else None
    if before == body:
        return "current"
    file.write_text(body, encoding="utf-8")
    return "created" if before is None else "rewritten"


def rewrite(service: Service, found: records.Surface) -> list[str]:
    lines = []
    for name, body in (("tests/surface.yaml", found.encode()), ("tests/checks.yaml", found.encode_checks())):
        lines.append(("~ " if records.rewrite(service.root / name, body) else "= ") + name)
    return lines


def tool_version() -> str:
    try:
        return version("specrig")
    except PackageNotFoundError:
        return "dev"


def resolve(dir: str, cwd: Path) -> Path:
    path = Path(dir)
    return path if path.is_absolute() else cwd / path


def parse(args: list[str], known: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    positional: list[str] = []
    flags: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        if args[i] in known:
            if i + 1 >= len(args):
                raise ValueError(f"{args[i]} needs a value")
            flags.setdefault(args[i], []).append(args[i + 1])
            i += 2
            continue
        if args[i].startswith("--"):
            raise ValueError(f"unknown argument {args[i]}")
        positional.append(args[i])
        i += 1
    return positional, flags
