from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .records import Trace

_DEFAULT_PORTS = {"http": 80, "https": 443}
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        raise ValueError(f"{url} is no absolute address")
    scheme = parts.scheme.lower()
    port = f":{parts.port}" if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{parts.hostname.lower()}{port}"


class Unreachable(Exception):
    def __init__(self, message: str, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def origin(self) -> str:
        return origin(self.url)

    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    def search(self) -> str:
        query = urlsplit(self.url).query
        return f"?{query}" if query else ""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def json(self) -> Any:
        return None if self.body == b"" else json.loads(self.body)


def query(search: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for name, value in parse_qsl(search.lstrip("?"), keep_blank_values=True):
        values.setdefault(name, []).append(value)
    return values


# A JSON value as the records hold it; a body that is not JSON is its text.
def decoded(raw: bytes | str) -> Any:
    if raw in (b"", ""):
        return None
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        return json.loads(text)
    except ValueError:
        return text


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Client:
    def __init__(self, handle: Callable[[Request], Response], headers: Mapping[str, str] | None = None) -> None:
        self._handle = handle
        self._headers = dict(headers or {"Accept": "application/json"})

    def with_headers(self, headers: Mapping[str, str]) -> Client:
        return Client(self._handle, {**self._headers, **headers})

    def get(self, uri: str, headers: Mapping[str, str] | None = None) -> Response:
        return self.send("GET", uri, None, headers)

    def delete(self, uri: str, headers: Mapping[str, str] | None = None) -> Response:
        return self.send("DELETE", uri, None, headers)

    def post(self, uri: str, body: Any = None, headers: Mapping[str, str] | None = None) -> Response:
        return self.send("POST", uri, body, headers)

    def put(self, uri: str, body: Any = None, headers: Mapping[str, str] | None = None) -> Response:
        return self.send("PUT", uri, body, headers)

    def patch(self, uri: str, body: Any = None, headers: Mapping[str, str] | None = None) -> Response:
        return self.send("PATCH", uri, body, headers)

    def send(self, method: str, uri: str, body: Any = None, headers: Mapping[str, str] | None = None) -> Response:
        sent = {**self._headers, **(headers or {})}
        if body is None:
            raw = b""
        elif isinstance(body, bytes):
            raw = body
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = encoded(body)
            sent.setdefault("Content-Type", "application/json")
        url = uri if re.match(r"^[a-z][a-z0-9+.-]*://", uri, re.IGNORECASE) else "http://testserver/" + uri.lstrip("/")
        return self._handle(Request(method.upper(), url, sent, raw))


def exchange(trace: Trace, request: Request, handle: Callable[[Request], tuple[Response, str | None]]) -> Response:
    call = trace.begin()
    name = f"{request.method} {request.path()}"
    status, body = 500, b""
    try:
        response, route = handle(request)
        if route is not None:
            name = f"{request.method} {route}"
        status, body = response.status, response.body
        return response
    finally:
        trace.end(call, "in", "http", name, _received(request), _refusal(status, body) if status >= 400 else None)


# As the Go record names a route: parameters without their converters, one that
# takes the rest of the path *.
def route(path: str, catch_all: Callable[[str, str], bool] = lambda name, kind: False) -> str:
    def named(m: re.Match[str]) -> str:
        name, kind = m.group(1), m.group(2) or ""
        return "*" if catch_all(name, kind) else f"{{{name}}}"

    template = re.sub(r"\{([^}:]+)(?::([^}]*))?\}", named, path)
    return "/" + "/".join(s for s in template.split("/") if s != "")


def _received(request: Request) -> Any:
    if request.body:
        return decoded(request.body)
    asked = query(request.search())
    return asked or None


# A framework's own problem types are links to the RFC, which name no refusal.
def _refusal(status: int, body: bytes) -> str:
    refusal = f"HTTP {status}"
    answer = decoded(body)
    kind = answer.get("type") if isinstance(answer, dict) else None
    if isinstance(kind, str) and kind != "" and not _SCHEME.match(kind):
        return f"{refusal} {kind}"
    return refusal
