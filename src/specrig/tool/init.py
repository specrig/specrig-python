from __future__ import annotations

import ast
import os
import re
import secrets
import sys
from pathlib import Path

import tomlkit

from .. import surface as records
from ..code import files, read
from ..code.index import Code
from . import agents, templates
from .cli import WRITTEN, parse, resolve, rewrite, write_specrig_md
from .service import GUARD, Service
from .templates import Backend, Plan, Resource

_POSTGRES = {"psycopg", "psycopg-binary", "psycopg2", "psycopg2-binary", "asyncpg"}
_MYSQL = {"mysqlclient", "pymysql", "asyncmy", "aiomysql"}
_SCHEMES = {
    "Postgres": (
        "postgresql",
        {"asyncpg": "postgresql+asyncpg", "psycopg": "postgresql+psycopg", "psycopg-binary": "postgresql+psycopg"},
    ),
    "MySQL": ("mysql", {"asyncmy": "mysql+asyncmy", "aiomysql": "mysql+aiomysql", "pymysql": "mysql+pymysql"}),
    "SQLite": ("sqlite", {"aiosqlite": "sqlite+aiosqlite"}),
}
_EXTRAS = {"Postgres": "postgres", "MySQL": "mysql", "SQLite": "sqlite"}
_ENGINES = {
    "postgresql": "Postgres",
    "postgis": "Postgres",
    "mysql": "MySQL",
    "sqlite3": "SQLite",
    "spatialite": "SQLite",
}


def init(args: list[str], cwd: Path) -> tuple[int, str]:
    positional, flags = parse(args, ["--backend", "--contract", "--failure", "--service"])
    service = Service(resolve(positional[0] if positional else ".", cwd))
    framework = service.framework()
    name = flags.get("--service", [service.name()])[0]
    found = read(service, name)
    plan = _plan(service, framework, name, found, flags)
    return 0, "\n".join(_write(plan, found))


def _plan(service: Service, framework: str, name: str, found: records.Surface, flags: dict[str, list[str]]) -> Plan:
    read_ = found.settings | found.optional
    contracts = _pairs(flags.get("--contract", []), "--contract")
    failures = _pairs(flags.get("--failure", []), "--failure")
    backends = []
    for given in flags.get("--backend", []):
        backend, _, setting = given.partition("=")
        if setting and setting not in read_:
            reads = ", ".join(sorted(read_)) or "nothing"
            raise ValueError(f"{name} reads no setting {setting}; it reads {reads}")
        backends.append(
            Backend(
                backend,
                _attribute(backend),
                f"https://{backend}.{name}.test",
                setting or None,
                contracts.get(backend),
                failures.get(backend),
            )
        )
    for named in [*contracts, *failures]:
        if named not in (b.name for b in backends):
            raise ValueError(f"{named} is not a backend; name it with --backend first")
    if not backends and found.calls:
        raise ValueError(
            f"{name} calls {', '.join(sorted(found.calls))}: name the neighbour with --backend <name>[=<setting>]"
        )

    resources = _resources(service, framework, read_)
    answered = {b.setting for b in backends if b.setting} | {r.setting for r in resources}
    constants = {}
    for setting in sorted(found.settings - answered):
        constants[setting] = secrets.token_urlsafe(32) if framework == "django" and setting == "SECRET_KEY" else ""
    target = _settings_module(service) if framework == "django" else _app(service)
    return Plan(service, name, framework, target, backends, resources, constants)


def _resources(service: Service, framework: str, read_: set[str]) -> list[Resource]:
    dependencies = service.dependencies()
    example = _example(service)
    resources = []
    database = next((s for s in sorted(read_) if s == "DATABASE_URL"), None) or next(
        (s for s in sorted(read_) if "DATABASE" in s and s.endswith("URL")), None
    )
    if database is not None and (found := _database(service, framework, dependencies, example.get(database, ""))):
        kind, scheme = found
        resources.append(Resource(kind, "database", database, None if scheme == _SCHEMES[kind][0] else scheme))
    redis = next((s for s in sorted(read_) if "REDIS" in s), None)
    if redis is not None and dependencies & {"redis", "django-redis"}:
        resources.append(Resource("Redis", "cache", redis))
    return resources


def urlscheme(url: str) -> str | None:
    match = re.match(r"^([a-z][a-z0-9+.-]*)://", url.strip().strip("'\""))
    return None if match is None or _dialect(match.group(1)) is None else match.group(1)


def _dialect(scheme: str) -> str | None:
    prefixes = {"Postgres": "postgres", "MySQL": "mysql", "SQLite": "sqlite"}
    return next((kind for kind, prefix in prefixes.items() if scheme.startswith(prefix)), None)


def _database(service: Service, framework: str, dependencies: set[str], example: str) -> tuple[str, str] | None:
    given = urlscheme(example)
    named = None if given is None else _dialect(given)
    servers = [kind for kind, drivers in (("Postgres", _POSTGRES), ("MySQL", _MYSQL)) if dependencies & drivers]
    engine = _engine(service) if framework == "django" else None
    if engine is not None:
        kind = engine
    elif servers:
        kind = named if named in servers else servers[0]
    elif named == "SQLite" or "aiosqlite" in dependencies:
        kind = "SQLite"
    else:
        return None
    if given is not None and named == kind:
        return kind, given
    default, drivers = _SCHEMES[kind]
    sqlalchemy = framework != "django" and "sqlalchemy" in dependencies
    return kind, next((s for d, s in drivers.items() if sqlalchemy and d in dependencies), default)


def _engine(service: Service) -> str | None:
    module = Path(*service.django_settings().split("."))
    file = next((f for f in (module.with_suffix(".py"), module / "__init__.py") if (service.root / f).is_file()), None)
    if file is None:
        return None
    engines = set()
    for node in ast.walk(ast.parse((service.root / file).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "ENGINE"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    engines.add(_ENGINES.get(value.value.rsplit(".", 1)[-1]))
    return engines.pop() if len(engines) == 1 else None


def _example(service: Service) -> dict[str, str]:
    values = {}
    for name in (".env.example", ".env.dist", ".env.sample"):
        file = service.root / name
        if file.is_file():
            for line in file.read_text(encoding="utf-8").splitlines():
                key, _, value = line.removeprefix("export ").partition("=")
                if key.strip() and not key.strip().startswith("#"):
                    values.setdefault(key.strip(), value.strip())
    return values


def _app(service: Service) -> str:
    code = Code(service.root, files(service.root))

    def builds(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and code.qualified(node.func) in (
            "fastapi.FastAPI",
            "fastapi.applications.FastAPI",
        )

    apps, factories = [], []
    for name, value in code.definitions.items():
        module, _, attribute = name.rpartition(".")
        if module not in code.modules:
            continue
        if builds(value):
            apps.append(f"{module}:{attribute}")
        elif isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(builds(n) for n in ast.walk(value)):
            factories.append(f"{module}:{attribute}")
    apps = apps or factories
    if not apps:
        raise ValueError(f"{service.root} builds no FastAPI application: name it in the harness's app")
    apps.sort(key=lambda a: (a.split(":")[0].rsplit(".", 1)[-1] not in ("main", "app", "asgi"), a))
    return apps[0]


def _settings_module(service: Service) -> str:
    return service.django_settings()


def _write(plan: Plan, found: records.Surface) -> list[str]:
    root = plan.service.root
    guard, note = _guard(root)
    written = {
        "tests/internal/harness.py": templates.harness(plan),
        "tests/internal/test_cases.py": templates.CASES,
        "tests/internal/boot.feature": templates.BOOT,
        "specrig.yaml": templates.config(guard),
    }
    lines = []
    for path, body in written.items():
        file = root / path
        if file.exists():
            lines.append(f"= {path} — kept")
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
        lines.append(f"+ {path}")
    for directory in ("tests/features", "tests/steps"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    service = Service(root)
    lines.append(_agents_md(service, plan.name))
    lines.append(WRITTEN[write_specrig_md(service)])
    claude = root / "CLAUDE.md"
    if not claude.exists() and not claude.is_symlink():
        claude.symlink_to("AGENTS.md")
        lines.append("+ CLAUDE.md → AGENTS.md")
    if service.config.get("guard") is None:
        lines.append(
            f"  specrig.yaml names no guard: add {templates.config(guard).strip()} for the modes to be enforced"
        )
    if note:
        lines.append(note)
    lines += rewrite(service, found)
    lines += _pyproject(plan)
    return lines


# The plugin runs the tool the marker names, the service's own: the one this run of
# init is, where it lives in the repository.
def _guard(root: Path) -> tuple[list[str], str | None]:
    tool = Path(sys.prefix) / "bin" / "specrig"
    repository = next((at for at in [root, *root.parents] if (at / ".git").exists()), root)
    if sys.prefix != sys.base_prefix and Path(sys.prefix).resolve().is_relative_to(repository) and tool.exists():
        return [Path(os.path.relpath(tool, root)).as_posix()], None
    return list(
        GUARD
    ), f"  specrig.yaml names {GUARD[0]}: the plugin runs it, so install specrig in the service's .venv"


def _agents_md(service: Service, name: str) -> str:
    file = service.root / "AGENTS.md"
    if not file.is_file():
        file.write_text(templates.agents_stub(name, (service.root.parent / "AGENTS.md").is_file()), encoding="utf-8")
        return "+ AGENTS.md"
    text = file.read_text(encoding="utf-8")
    left = (
        "; it still carries Rule #11, which specrig.md holds now: keep the service's own sections in it and delete the rest"
        if agents.verdict(text) == "stale"
        else ""
    )
    if agents.references(text):
        return f"= AGENTS.md — kept{left}"
    gap = "" if text == "" else ("\n" if text.endswith("\n") else "\n\n")
    file.write_text(text + gap + templates.reference() + "\n", encoding="utf-8")
    return f"~ AGENTS.md: the line that brings specrig.md in, at its end{left}"


def _pyproject(plan: Plan) -> list[str]:
    file = plan.service.root / "pyproject.toml"
    if "specrig" in plan.service.dependencies():
        return []
    extras = [plan.framework, *(_EXTRAS[r.kind] for r in plan.resources if r.kind in _EXTRAS)]
    requirement = f"specrig[{','.join(extras)}]>=0.1"
    if not file.is_file():
        return [f"  there is no pyproject.toml: add {requirement} to the service's dev requirements"]
    document = tomlkit.parse(file.read_text(encoding="utf-8"))
    poetry = document.get("tool", {}).get("poetry")
    if poetry is not None and "project" not in document:
        group = poetry.setdefault("group", tomlkit.table()).setdefault("dev", tomlkit.table())
        dependencies = group.setdefault("dependencies", tomlkit.table())
        dependencies["specrig"] = {"version": ">=0.1", "extras": extras}
        where = "[tool.poetry.group.dev.dependencies]"
    else:
        groups = document.setdefault("dependency-groups", tomlkit.table())
        dev = groups.setdefault("dev", tomlkit.array())
        dev.append(requirement)
        where = "[dependency-groups] dev"
    file.write_text(tomlkit.dumps(document), encoding="utf-8")
    return [f"~ pyproject.toml: {requirement} in {where}", "  then: install the dev dependencies as the project does"]


def _attribute(name: str) -> str:
    snake = re.sub(r"[^0-9a-zA-Z]+", "_", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)).strip("_").lower()
    return f"_{snake}" if not snake or snake[0].isdigit() else snake


def _pairs(given: list[str], flag: str) -> dict[str, str]:
    pairs = {}
    for pair in given:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"{flag} wants <name>=<value>")
        pairs[key] = value
    return pairs
