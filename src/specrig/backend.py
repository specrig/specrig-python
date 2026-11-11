from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from . import http
from .contract import Contract
from .http import Request, Response, Unreachable
from .records import Trace

T = TypeVar("T")

Query = Mapping[str, str | int | Sequence[str | int]]

Headers = Mapping[str, str]


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    query: dict[str, list[str]]
    json: Any


class StandIn(ABC):
    origin: str
    setting: str | None

    @abstractmethod
    def attach(self, name: str, trace: Trace, root: Path) -> None: ...

    @abstractmethod
    def answer(self, request: Request) -> Response: ...

    @abstractmethod
    def failures(self) -> list[str]: ...

    @abstractmethod
    def fresh(self) -> StandIn: ...


class Backend(StandIn):
    def __init__(
        self, origin: str, setting: str | None = None, contract: str | None = None, failure: str | None = None
    ) -> None:
        self.origin = http.origin(origin)
        self.setting = setting
        self._contract = contract
        self._failure = failure
        self.name = ""
        self._trace = Trace()
        self._checked: Contract | None = None
        self._replies: dict[str, list[tuple[dict[str, list[str]], int, Any, dict[str, str]]]] = {}
        self._timeouts: dict[str, list[dict[str, list[str]]]] = {}
        self._received: list[Received] = []
        self._unanswered: list[str] = []
        self._violations: list[str] = []

    def fresh(self) -> Backend:
        return Backend(self.origin, self.setting, self._contract, self._failure)

    def attach(self, name: str, trace: Trace, root: Path) -> None:
        self.name = name
        self._trace = trace
        if self._contract is not None:
            self._checked = Contract.load((root / self._contract).resolve(), name, self._failure)

    def failures(self) -> list[str]:
        failures = []
        if self._unanswered:
            failures.append(f"{self.name} was called where no step declared its answer: {', '.join(self._unanswered)}")
        if self._violations:
            failures.append(
                f"what {self.name}'s contract, {self._contract}, does not allow: {'; '.join(self._violations)}"
            )
        return failures

    def reply(self, method: str, path: str, status: int, body: Any = None, headers: Headers | None = None) -> None:
        self.reply_to(method, path, {}, status, body, headers)

    def reply_to(
        self, method: str, path: str, query: Query, status: int, body: Any = None, headers: Headers | None = None
    ) -> None:
        key = f"{method.upper()} {path}"
        asked = _listed(query)
        kept = [r for r in self._replies.get(key, []) if r[0] != asked]
        self._replies[key] = [*kept, (asked, status, copy.deepcopy(body), dict(headers or {}))]

    def time_out(self, method: str, path: str, query: Query | None = None) -> None:
        key = f"{method.upper()} {path}"
        asked = _listed(query or {})
        self._timeouts[key] = [*(q for q in self._timeouts.get(key, []) if q != asked), asked]

    def requests(self, method: str, path: str) -> list[Received]:
        return [r for r in self._received if r.method == method.upper() and _takes(path, r.path)]

    def answer(self, request: Request) -> Response:
        method = request.method.upper()
        path = request.path()
        key = f"{method} {path}"
        asked = http.query(request.search())
        body = http.decoded(request.body)
        call = self._trace.begin()
        try:
            self._received.append(Received(method, path, asked, body))
            if self._checked is not None:
                self._violations.extend(self._checked.request(method, path, body))
            if _pick(_declared(self._timeouts, method, path), asked, lambda q: q) is not None:
                raise Unreachable(f"{self.name} did not answer {key} in time", timed_out=True)
            return self._fulfil(method, path, key + request.search(), asked)
        finally:
            name = self._checked.name(method, path) if self._checked is not None else key
            self._trace.end(call, "out", "http", name, body)

    def _fulfil(self, method: str, path: str, asked: str, query: dict[str, list[str]]) -> Response:
        reply = _pick(_declared(self._replies, method, path), query, lambda r: r[0])
        if reply is None:
            self._unanswered.append(asked)
            return _respond(501, {"code": "unanswered", "message": f"{method} {path}"})
        _, status, body, headers = reply
        if self._checked is not None:
            self._violations.extend(self._checked.response(method, path, status, {} if body is None else body))
        return _respond(status, None if status == 204 else ({} if body is None else body), headers)


def _respond(status: int, body: Any, headers: Headers | None = None) -> Response:
    given = dict(headers or {})
    if body is None:
        return Response(status, given)
    typed = {} if "content-type" in {name.lower() for name in given} else {"Content-Type": "application/json"}
    return Response(status, {**typed, **given}, http.encoded(body))


def _declared(table: dict[str, list[T]], method: str, path: str) -> list[T]:
    exact = table.get(f"{method} {path}")
    if exact is not None:
        return exact
    prefix = f"{method} "
    for key, found in table.items():
        if key.startswith(prefix) and _takes(key[len(prefix) :], path):
            return found
    return []


def _takes(template: str, path: str) -> bool:
    segments, wanted = template.split("/"), path.split("/")
    return len(segments) == len(wanted) and all(
        (s.startswith("{") and s.endswith("}")) or s == w for s, w in zip(segments, wanted, strict=True)
    )


def _pick(candidates: list[T], query: dict[str, list[str]], of: Callable[[T], dict[str, list[str]]]) -> T | None:
    found: T | None = None
    for candidate in candidates:
        if _matches(of(candidate), query) and len(of(candidate)) >= (0 if found is None else len(of(found))):
            found = candidate
    return found


def _matches(wanted: dict[str, list[str]], got: dict[str, list[str]]) -> bool:
    for name, values in wanted.items():
        have = got.get(name)
        if have is None or (have[:1] != values if len(values) == 1 else have != values):
            return False
    return True


def _listed(query: Query) -> dict[str, list[str]]:
    return {
        name: [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
        for name, value in query.items()
    }
