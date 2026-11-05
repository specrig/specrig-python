from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from . import run as runs
from . import standins
from .backend import StandIn
from .clock import Clock
from .http import Client
from .proxy import Proxy
from .records import Trace
from .resource import Databases, Resource
from .standins import StandIns
from .surface import Checks

T = TypeVar("T")


@dataclass(frozen=True)
class Logged:
    level: str
    message: str
    logger: str


class _Logs(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.records: list[Logged] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(Logged(record.levelname.lower(), record.getMessage(), record.name))


@dataclass
class _Run:
    answered: list[str] = field(default_factory=list)
    answers: dict[str, str | None] = field(default_factory=dict)
    proxy: Proxy | None = None
    started: bool = False
    broken: BaseException | None = None
    cases: int = 0


_runs: dict[type, _Run] = {}


def named(target: Any) -> str:
    if isinstance(target, str):
        return target
    return f"{target.__module__}:{target.__qualname__}"


def resolve(target: str) -> Any:
    module, _, attribute = target.partition(":")
    found: Any = importlib.import_module(module)
    for part in attribute.split(".") if attribute else []:
        found = getattr(found, part)
    return found


def runnable(target: Any) -> Callable[..., Any]:
    if not inspect.isclass(target):
        return target
    instance = target()
    if callable(instance):
        return instance
    for method in ("run", "execute", "handle"):
        if hasattr(instance, method):
            return getattr(instance, method)
    raise RuntimeError(f"{target.__qualname__} has no __call__, run, execute or handle to run it by")


class Rig:
    service: ClassVar[str]
    constants: ClassVar[Mapping[str, str | None]] = {}
    allow: ClassVar[Sequence[str]] = ()

    def __init__(self) -> None:
        self.trace = Trace()
        self.clock = Clock()
        self._neighbours: dict[str, StandIn] = {}
        self._resources: dict[str, Resource] = {}
        for name, declared in _declared(type(self)):
            fresh = declared.fresh()
            setattr(self, name, fresh)
            if isinstance(fresh, StandIn):
                self._neighbours[name] = fresh
            else:
                self._resources[name] = fresh
        for name, neighbour in self._neighbours.items():
            neighbour.attach(name, self.trace, self.root())
        for resource in self._resources.values():
            resource.bind(self.service)
        self._stand_ins = StandIns(self._neighbours.values())
        self._replaced: dict[str, Any] = {}
        self._patched: list[tuple[Any, str, Any]] = []
        self._booted = False
        self._logs = _Logs()
        self.output = ""
        self._run = type(self).prepare()
        assert self._run.proxy is not None
        self._run.proxy.mark()
        self._run.cases += 1
        # The service is imported once per run, so what it keeps by its caller's
        # address — a rate limit — would carry the last case's calls into this one.
        n = self._run.cases
        self.address = f"127.{n >> 16 & 255}.{n >> 8 & 255}.{n & 255}"

    @classmethod
    def root(cls) -> Path:
        return Path(inspect.getfile(cls)).resolve().parents[2]

    @classmethod
    def tests(cls) -> Path:
        return cls.root() / "tests"

    # The environment is the service's before any of its modules is imported, the
    # steps' included: a module that reads a setting as it loads reads the answer.
    @classmethod
    def prepare(cls) -> _Run:
        if cls in _runs:
            state = _runs[cls]
            if state.broken is not None:
                raise RuntimeError(f"{cls.service} did not boot: {state.broken}") from state.broken
            return state
        state = _runs[cls] = _Run()
        try:
            cls._set(state)
            cls._check(state.answered)
            cls._prepared()
        except BaseException as e:
            state.broken = e
            raise
        return state

    @classmethod
    def _set(cls, state: _Run) -> None:
        root = str(cls.root())
        if root not in sys.path:
            sys.path.insert(0, root)
        state.proxy = Proxy()
        runs.ending(state.proxy.stop)
        standins.serve(None)
        answers: dict[str, str | None] = {}
        for _, declared in _declared(cls):
            if isinstance(declared, StandIn) and declared.setting is not None:
                answers[declared.setting] = declared.origin
            elif isinstance(declared, Resource):
                answers[declared.setting] = declared.bind(cls.service)
        answers.update(cls.constants)
        checks = Checks.of(cls.tests() / "checks.yaml")
        for unanswered in checks.read() if checks else []:
            if unanswered not in answers:
                os.environ.pop(unanswered, None)
        for name, value in {**answers, **cls._framework(), **state.proxy.environment()}.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        state.answers = dict(answers)
        state.answered = list(answers)

    @classmethod
    def _framework(cls) -> dict[str, str | None]:
        return {}

    @classmethod
    def _prepared(cls) -> None:
        pass

    def _first(self) -> None:
        raise NotImplementedError

    def _start(self) -> None:
        raise NotImplementedError

    def _stop(self) -> None:
        raise NotImplementedError

    def databases(self) -> Databases:
        raise NotImplementedError

    def http(self) -> Client:
        raise NotImplementedError

    def run(self, job: Any, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def command(self, name: str, *args: str) -> int:
        raise NotImplementedError

    @property
    def logs(self) -> list[Logged]:
        return list(self._logs.records)

    @property
    def booted(self) -> bool:
        return self._booted

    def replace(self, target: Any, instance: Any) -> None:
        name = named(target)
        if self._booted:
            raise RuntimeError(
                f"{self.service} has booted: replace {name} in a Given, before a step reaches the service"
            )
        if name not in self.allow:
            raise RuntimeError(f"{name} is not granted: allow in {type(self).__name__} does not name it")
        self._replaced[name] = instance

    def boot(self) -> None:
        if self._booted:
            return
        state = self._run
        if not state.started and state.broken is None:
            try:
                self._kept()
                for resource in self._resources.values():
                    resource.provision()
                self._first()
                self._kept()
                state.started = True
            except BaseException as e:
                state.broken = e
                raise
        if state.broken is not None:
            raise RuntimeError(f"{self.service} did not boot: {state.broken}") from state.broken
        self._kept()
        standins.serve(self._stand_ins)
        logging.getLogger().addHandler(self._logs)
        self._booted = True
        try:
            self._forget()
            for resource in self._resources.values():
                resource.ready(self)
            self.seed()
            self._grant()
            self._start()
        except BaseException:
            self.dispose()
            raise
        for resource in self._resources.values():
            resource.started()

    def seed(self) -> None:
        pass

    # A service that loads a file over its environment as it imports — a .env read
    # with override — would send the cases where the file points: the run stops.
    def _kept(self) -> None:
        changed = [
            name for name, value in self._run.answers.items() if value is not None and os.environ.get(name) != value
        ]
        if changed:
            raise RuntimeError(
                f"{', '.join(changed)} changed after {type(self).__name__} answered it: the service loads a file "
                "over its environment, a .env say, and the cases would reach what the file names"
            )

    def failures(self) -> list[str]:
        assert self._run.proxy is not None
        refused = list(dict.fromkeys(self._run.proxy.refused()))
        outside = [*self._stand_ins.outside(), *(f"{r}, past the stand-ins" for r in refused)]
        failures = (
            [f"requests to an origin {type(self).__name__} does not name: {', '.join(outside)}"] if outside else []
        )
        for neighbour in self._neighbours.values():
            failures.extend(neighbour.failures())
        for resource in self._resources.values():
            failures.extend(resource.failures())
        return failures

    def dispose(self) -> None:
        try:
            if self._booted:
                self._booted = False
                try:
                    self._stop()
                finally:
                    self._ungrant()
                    logging.getLogger().removeHandler(self._logs)
                    standins.serve(None)
        finally:
            for resource in self._resources.values():
                resource.release()
            self.clock.release()

    def traced(self, kind: str, name: str, request: Any, call: Callable[[], T], exits: bool = False) -> T:
        self.boot()
        began = self.trace.begin()
        error = None
        try:
            result = call()
            if exits and isinstance(result, int) and result != 0:
                error = f"exit {result}"
            return result
        except BaseException as e:
            error = type(e).__name__
            raise
        finally:
            self.trace.end(began, "in", kind, name, request, error)

    @classmethod
    def _check(cls, answered: list[str]) -> None:
        checks = Checks.of(cls.tests() / "checks.yaml")
        if checks is None:
            return
        wrong = []
        unanswered = [s for s in checks.settings if s not in answered]
        if unanswered:
            wrong.append(
                f"{cls.service} reads {', '.join(unanswered)}, which {cls.__name__} does not answer: answer it in constants"
            )
        unread = checks.unread(answered)
        if unread:
            wrong.append(f"{cls.__name__} answers {', '.join(unread)}, which {cls.service} does not read")
        if wrong:
            raise RuntimeError("\n".join(wrong))

    def _granted(self) -> dict[str, tuple[Any, Any]]:
        return {name: (resolve(name), instance) for name, instance in self._replaced.items()}

    def _modules(self) -> list[Any]:
        root = self.root()
        tests = root / "tests"
        found = []
        for module in list(sys.modules.values()):
            file = getattr(module, "__file__", None)
            if file is not None and Path(file).is_relative_to(root) and not Path(file).is_relative_to(tests):
                found.append(module)
        return found

    # The service is imported once per run, so what it caches with functools is
    # forgotten as a new process would forget it.
    def _forget(self) -> None:
        for module in self._modules():
            for value in list(vars(module).values()):
                if hasattr(value, "__wrapped__") and callable(getattr(value, "cache_clear", None)):
                    value.cache_clear()

    # A granted name is replaced wherever the service's modules hold it.
    def _grant(self) -> None:
        for target, instance in self._granted().values():
            for module in self._modules():
                for attribute, value in list(vars(module).items()):
                    if value is target:
                        self._patched.append((module, attribute, value))
                        setattr(module, attribute, instance)

    def _ungrant(self) -> None:
        for module, attribute, value in reversed(self._patched):
            setattr(module, attribute, value)
        self._patched = []


def _declared(cls: type) -> list[tuple[str, StandIn | Resource]]:
    found: dict[str, StandIn | Resource] = {}
    for klass in reversed(cls.__mro__):
        for name, value in vars(klass).items():
            if isinstance(value, (StandIn, Resource)):
                found[name] = value
    return list(found.items())
