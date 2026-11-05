from __future__ import annotations

import contextlib
import logging
import math
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

import anyio
import anyio.from_thread

Message = dict[str, Any]


# An ASGI server of the harness's own, in a thread of its own: the service's lifespan
# runs from the boot to the end of the case, and a request is answered whole — its
# background tasks included — before the step that sent it goes on.
class Server:
    def __init__(self, app: Callable[..., Any]) -> None:
        self.app = app
        self.state: dict[str, Any] = {}
        self._stack = contextlib.ExitStack()
        self.portal = self._stack.enter_context(anyio.from_thread.start_blocking_portal())
        self._to_app, self._app_reads = anyio.create_memory_object_stream[Message](math.inf)
        self._app_sends, self._from_app = anyio.create_memory_object_stream[Message | None](math.inf)
        self._lifespan = False

    def start(self) -> None:
        self.portal.start_task_soon(self._run_lifespan)
        answer = self.portal.call(self._lifespan_event, "startup")
        if answer is None:
            return
        if answer["type"] == "lifespan.startup.failed":
            raise RuntimeError(f"the service did not start: {answer.get('message', '')}")
        self._lifespan = True

    def stop(self) -> None:
        try:
            if self._lifespan:
                answer = self.portal.call(self._lifespan_event, "shutdown")
                if answer is not None and answer["type"] == "lifespan.shutdown.failed":
                    raise RuntimeError(f"the service did not stop: {answer.get('message', '')}")
        finally:
            self._stack.close()

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes, client: str = "127.0.0.1"
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        parts = urlsplit(url)
        host = parts.netloc or "testserver"
        sent = [(b"host", host.encode())]
        sent += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items() if k.lower() != "host"]
        if body and not any(k == b"content-length" for k, _ in sent):
            sent.append((b"content-length", str(len(body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": parts.scheme or "http",
            "path": unquote(parts.path) or "/",
            "raw_path": (parts.path or "/").encode(),
            "query_string": parts.query.encode(),
            "root_path": "",
            "headers": sent,
            "client": (client, 50000),
            "server": (parts.hostname or "testserver", parts.port or (443 if parts.scheme == "https" else 80)),
            "state": dict(self.state),
        }
        return self.portal.call(self._request, scope, body)

    async def _run_lifespan(self) -> None:
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": self.state}
        try:
            await self.app(scope, self._app_reads.receive, self._app_sends.send)
        except Exception:
            if self._lifespan:
                logging.getLogger("specrig").exception("the service's lifespan failed")
        finally:
            await self._app_sends.send(None)

    async def _lifespan_event(self, event: str) -> Message | None:
        await self._to_app.send({"type": f"lifespan.{event}"})
        return await self._from_app.receive()

    async def _request(self, scope: dict[str, Any], body: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
        read = False
        done = anyio.Event()
        status: int | None = None
        headers: list[tuple[str, str]] = []
        chunks: list[bytes] = []

        async def receive() -> Message:
            nonlocal read
            if not read:
                read = True
                return {"type": "http.request", "body": body, "more_body": False}
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            nonlocal status, headers
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    done.set()

        try:
            await self.app(scope, receive, send)
        except Exception:
            logging.getLogger("specrig").exception("the service failed on %s %s", scope["method"], scope["path"])
            if status is None:
                return 500, [], b""
        finally:
            done.set()
        return status or 500, headers, b"".join(chunks)
