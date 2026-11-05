from __future__ import annotations

import importlib
import threading
from collections.abc import Iterable
from typing import Any

from .backend import StandIn
from .http import Request, Response, Unreachable


class StandIns:
    def __init__(self, stand_ins: Iterable[StandIn]) -> None:
        self._stand_ins = list(stand_ins)
        self._outside: list[str] = []
        self._lock = threading.Lock()

    def outside(self) -> list[str]:
        return list(self._outside)

    def send(self, request: Request) -> Response:
        at = request.origin()
        for stand_in in self._stand_ins:
            if stand_in.origin == at:
                return stand_in.answer(request)
        with self._lock:
            self._outside.append(f"{request.method.upper()} {request.url}")
        raise Unreachable(f"{at} is no neighbour the harness names")


_current: StandIns | None = None
_installed = False


def serve(stand_ins: StandIns | None) -> None:
    global _current
    _current = stand_ins
    _install()


def _send(request: Request) -> Response:
    if _current is None:
        raise Unreachable(f"{request.origin()}: the service sent it while no case was running")
    return _current.send(request)


# Under every client the service builds, whoever builds it: httpx's transports and
# requests' adapter are where a request leaves.
def _install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    for name in ("httpx", "httpx2"):
        try:
            _httpx(importlib.import_module(name))
        except ImportError:
            pass
    try:
        import requests.adapters
    except ImportError:
        pass
    else:
        _requests(requests)


def _httpx(httpx: Any) -> None:
    def answered(request: Any) -> Any:
        sent = Request(request.method, str(request.url), dict(request.headers), request.read())
        try:
            answer = _send(sent)
        except Unreachable as e:
            raise (httpx.ReadTimeout if e.timed_out else httpx.ConnectError)(str(e), request=request) from None
        return httpx.Response(answer.status, headers=dict(answer.headers), content=answer.body, request=request)

    def handle_request(self: Any, request: Any) -> Any:
        return answered(request)

    async def handle_async_request(self: Any, request: Any) -> Any:
        await request.aread()
        return answered(request)

    httpx.HTTPTransport.handle_request = handle_request
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request


def _requests(requests: Any) -> None:
    from requests.structures import CaseInsensitiveDict

    def send(self: Any, request: Any, timeout: Any = None, **kwargs: Any) -> Any:
        body = request.body or b""
        sent = Request(
            request.method, request.url, dict(request.headers), body.encode() if isinstance(body, str) else body
        )
        try:
            answer = _send(sent)
        except Unreachable as e:
            raise (requests.exceptions.ReadTimeout if e.timed_out else requests.exceptions.ConnectionError)(
                str(e), request=request
            ) from None
        response = requests.Response()
        response.status_code = answer.status
        response.headers = CaseInsensitiveDict(answer.headers)
        response._content = answer.body
        response.url = request.url
        response.request = request
        response.reason = ""
        response.encoding = "utf-8"
        response.connection = self
        return response

    requests.adapters.HTTPAdapter.send = send
