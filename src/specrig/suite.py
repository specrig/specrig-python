from __future__ import annotations

import importlib
import sys
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import when
from pytest_bdd.feature import get_features

from . import flows
from . import run as runs
from .records import Record, Run
from .rig import Rig

_KINDS = {"given": "Context", "when": "Action", "then": "Outcome"}


@dataclass
class _Case:
    harness: Rig
    label: str | None


@dataclass
class _Suite:
    root: Path
    harness: type[Rig]
    runs: list[Run] = field(default_factory=list)
    ran: set[tuple[str | None, tuple[tuple[str, str], ...]]] = field(default_factory=set)

    @property
    def features(self) -> Path:
        return self.root / "tests" / "features"

    def label(self, feature: str, title: str) -> str | None:
        path = Path(feature).resolve()
        if not path.is_relative_to(self.features.resolve()):
            return None
        return f"{path.relative_to(self.features.resolve()).as_posix()}: {title}"

    def ended(self, label: str, row: dict[str, str], records: list[Record]) -> None:
        self.runs.append(Run(label, records))
        self.ran.add((label, tuple(row.items())))

    # A run narrowed to some cases saw part of the service, and its record would lose the rest.
    def whole(self) -> bool:
        if not self.features.is_dir():
            return False
        for feature in get_features([str(self.features)]):
            for template in feature.scenarios.values():
                rule = template.rule.tags if template.rule is not None else set()
                if "ignore" in template.tags | feature.tags | rule:
                    continue
                for row in _rows(template):
                    label = self.label(feature.filename, template.render(row).name)
                    if (label, tuple(row.items())) not in self.ran:
                        return False
        return bool(self.ran)

    def write(self) -> str | None:
        if not self.whole():
            return None
        if flows.write(self.root / "tests" / "flows.yaml", self.harness.service, self.runs) > 0:
            return (
                "tests/flows.yaml: some descriptions lost their anchor — delete them or move them "
                "under the input or branch they belong to now"
            )
        return None


_suites: dict[Path, _Suite] = {}
_case = pytest.StashKey[_Case]()


def _root(path: Path) -> Path | None:
    for at in [path, *path.parents]:
        if (at / "specrig.yaml").is_file() and (at / "tests" / "internal" / "harness.py").is_file():
            return at
    return None


def _open(root: Path, config: pytest.Config) -> _Suite:
    if root in _suites:
        return _suites[root]
    if _suites:
        raise pytest.UsageError(
            f"{root} is a second service in one run, beside {next(iter(_suites))}: run each service's suite on its own"
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = importlib.import_module("tests.internal.harness")
    harness = getattr(module, "Harness", None)
    if not isinstance(harness, type) or not issubclass(harness, Rig):
        raise pytest.UsageError(f"{module.__file__} defines no Harness, a specrig harness")
    suite = _suites[root] = _Suite(root, harness)
    try:
        harness.prepare()
    except RuntimeError as e:
        raise pytest.UsageError(str(e)) from e
    steps = root / "tests" / "steps"
    for file in sorted(steps.rglob("*.py")) if steps.is_dir() else []:
        relative = file.relative_to(root).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        name = ".".join(parts)
        imported = importlib.import_module(name)
        if not config.pluginmanager.is_registered(imported):
            config.pluginmanager.register(imported, f"specrig:{name}")
    return suite


def _suite_of(path: Path) -> _Suite | None:
    root = _root(path.resolve())
    return None if root is None else _suites.get(root)


def _given(args: Iterable[str], at: Path) -> list[Path]:
    paths = [Path(arg.split("::", 1)[0]) for arg in args]
    return [(p if p.is_absolute() else at / p).resolve() for p in paths] or [at.resolve()]


# The suite is a run of its own, named by its path: before the conftests, which may
# import the service, so a module that reads a setting as it loads reads the
# harness's answer. A run of the service's own tests is left as it was.
@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config, parser: pytest.Parser, args: list[str]) -> None:
    given = _given(early_config.known_args_namespace.file_or_dir or [], early_config.invocation_params.dir)
    for path in given:
        root = _root(path)
        if root is not None and path.is_relative_to(root / "tests" / "internal"):
            _open(root, early_config)
    # A conftest above the suite is the service's own tests', and its fixtures,
    # autouse ones included, would change the service the cases see.
    suites = [root / "tests" / "internal" for root in _suites]
    if suites and "--confcutdir" not in args and all(path.is_relative_to(suites[0]) for path in given):
        early_config.known_args_namespace.confcutdir = str(suites[0])


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "ignore: a case left out of the run")


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    root = _root(collection_path.resolve())
    if root is not None and root not in _suites and collection_path.resolve() == root / "tests" / "internal":
        return True
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    ignored = [i for i in items if _suite_of(i.path) is not None and i.get_closest_marker("ignore") is not None]
    if ignored:
        config.hook.pytest_deselected(items=ignored)
        items[:] = [i for i in items if i not in ignored]


@pytest.fixture(autouse=True)
def _specrig_case(request: pytest.FixtureRequest) -> Generator[None]:
    suite = _suite_of(request.path)
    template = _template(getattr(request, "function", None))
    if suite is None or template is None:
        yield
        return
    row = request.getfixturevalue("_pytest_bdd_example")
    case = _Case(suite.harness(), suite.label(template.feature.filename, template.render(row).name))
    request.node.stash[_case] = case
    try:
        yield
        if case.label is not None:
            suite.ended(case.label, row, case.harness.trace.records())
    finally:
        case.harness.dispose()


# pytest-bdd 9 keeps a test's scenario in a registry, 8 on the test itself.
def _template(function: Any) -> Any:
    registry = getattr(importlib.import_module("pytest_bdd.scenario"), "scenario_wrapper_template_registry", None)
    if registry is None:
        return getattr(function, "__scenario__", None)
    try:
        return registry.get(function)
    except TypeError:
        return None


# An outline runs as its rows, each under its title filled from the row.
def _rows(template: Any) -> list[dict[str, str]]:
    if not template.examples:
        return [{}]
    return [row for examples in template.examples if "ignore" not in examples.tags for row in examples.as_contexts()]


@pytest.fixture
def harness(request: pytest.FixtureRequest) -> Rig:
    case = request.node.stash.get(_case, None)
    if case is None:
        raise pytest.UsageError("the harness is a case's: a scenario of tests/features or tests/internal asks for it")
    return case.harness


def pytest_bdd_before_step(request: pytest.FixtureRequest, step: Any) -> None:
    case = request.node.stash.get(_case, None)
    if case is not None:
        case.harness.trace.act(_KINDS.get(step.type, "Outcome"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    case = item.stash.get(_case, None)
    try:
        result = yield
    except BaseException:
        if case is not None and (failures := case.harness.failures()):
            item.add_report_section("call", "specrig", "\n".join(failures))
        raise
    if case is not None and (failures := case.harness.failures()):
        pytest.fail("\n".join(failures), pytrace=False)
    return result


def pytest_sessionfinish(session: pytest.Session) -> None:
    try:
        for suite in _suites.values():
            wrong = suite.write()
            if wrong is not None:
                reporter = session.config.pluginmanager.get_plugin("terminalreporter")
                if reporter is not None:
                    reporter.write_line(f"Error: {wrong}", red=True)
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
    finally:
        runs.end()


@when("the service boots")
def _boots(harness: Rig) -> None:
    harness.boot()
