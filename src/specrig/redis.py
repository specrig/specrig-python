from __future__ import annotations

import os
import re
import socket
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from . import run
from .docker import Docker, DockerError
from .gate import Gate
from .resource import Resource

if TYPE_CHECKING:
    from .rig import Rig


class _Server:
    def __init__(self, service: str, given: str | None, image: str) -> None:
        self._service = service
        self._given = given
        self._image = image
        self._server = given
        self._lock = threading.Lock()
        self.gate = Gate(self._upstream)
        parts = urlsplit(given or "redis://")
        credentials = parts.netloc.rpartition("@")[0]
        self.address = f"redis://{credentials + '@' if credentials else ''}127.0.0.1:{self.gate.port}{parts.path}"

    def _upstream(self) -> tuple[str, int]:
        parts = urlsplit(self.start())
        return parts.hostname or "127.0.0.1", parts.port or 6379

    # Starting redis costs less than a second, so the container is the run's own rather than kept.
    def start(self) -> str:
        with self._lock:
            if self._server is None:
                self._server = self._container()
            return self._server

    def _container(self) -> str:
        docker = Docker()
        try:
            id = docker.run(self._image, 6379, [], {"specrig": "redis"})
            run.ending(lambda: docker.remove(id))
            address = f"redis://{Docker.host()}:{docker.port(id, 6379)}"
        except DockerError as e:
            raise RuntimeError(
                f"redis: no address is given and no container started — set {self._service}_TEST_REDIS, or start Docker: {e}"
            ) from e
        deadline = time.monotonic() + 30
        while True:
            try:
                _send(address, "PING")
                return address
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    def flush(self) -> None:
        _send(self.start(), "FLUSHDB")


_servers: dict[str, _Server] = {}


class Redis(Resource):
    def __init__(self, setting: str, address: str | None = None, image: str = "redis:8-alpine") -> None:
        self.setting = setting
        self._given = address
        self._image = image
        self._server: _Server | None = None
        self._started = False
        self._refusing = False

    def fresh(self) -> Redis:
        return Redis(self.setting, self._given, self._image)

    def bind(self, service: str) -> str:
        return self._of(service).address

    def provision(self) -> None:
        self._held().start()

    def ready(self, rig: Rig) -> None:
        self._held().flush()

    def started(self) -> None:
        self._started = True
        if self._refusing:
            self._held().gate.refuse()

    def fail_with(self) -> None:
        self._refusing = True
        if self._started:
            self._held().gate.refuse()

    def release(self) -> None:
        if self._server is not None:
            self._server.gate.reopen()
        self._started = False
        self._refusing = False

    def _of(self, service: str) -> _Server:
        if self._server is None:
            name = re.sub(r"[^A-Z0-9]", "_", service.upper())
            given = self._given or os.environ.get(f"{name}_TEST_REDIS")
            key = given or self._image
            if key not in _servers:
                _servers[key] = _Server(name, given, self._image)
            self._server = _servers[key]
        return self._server

    def _held(self) -> _Server:
        if self._server is None:
            raise RuntimeError("redis is not provisioned: the harness has not prepared the run")
        return self._server


def _send(address: str, *command: str) -> str:
    parts = urlsplit(address)
    host, port = parts.hostname or "127.0.0.1", parts.port or 6379
    try:
        return _exchange(socket.create_connection((host, port), timeout=5), parts, command)
    except OSError as e:
        raise RuntimeError(f"redis at {host}:{port}: {e}") from e


def _exchange(connection: socket.socket, parts: Any, command: tuple[str, ...]) -> str:
    with connection:
        commands: list[tuple[str, ...]] = []
        if parts.password is not None or parts.username:
            commands.append(
                ("AUTH", unquote(parts.username), unquote(parts.password or ""))
                if parts.username
                else ("AUTH", unquote(parts.password or ""))
            )
        database = parts.path.strip("/")
        if database not in ("", "0"):
            commands.append(("SELECT", database))
        commands.append(command)
        reply = ""
        reader = connection.makefile("rb")
        for words in commands:
            connection.sendall(
                f"*{len(words)}\r\n".encode() + b"".join(f"${len(w.encode())}\r\n{w}\r\n".encode() for w in words)
            )
            reply = reader.readline().decode()
            if not reply.startswith("+"):
                raise RuntimeError(f"redis at {parts.hostname}:{parts.port}: {reply.strip() or 'no reply'}")
        return reply[1:].strip()
