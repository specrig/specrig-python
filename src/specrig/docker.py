from __future__ import annotations

import http.client
import json
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


class DockerError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("docker", timeout=600)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class Docker:
    def __init__(self, socket_path: str | None = None) -> None:
        self._socket = socket_path or _socket()

    # Inside the suite's image the published ports are on the host, not on loopback.
    @staticmethod
    def host() -> str:
        return os.environ.get("TESTCONTAINERS_HOST_OVERRIDE") or "127.0.0.1"

    def containers(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        filters = json.dumps({"label": [f"{k}={v}" for k, v in labels.items()]})
        return self._call("GET", f"/containers/json?all=1&filters={quote(filters)}")

    def run(
        self,
        image: str,
        port: int,
        env: list[str],
        labels: dict[str, str],
        name: str | None = None,
        command: list[str] | None = None,
        loopback: bool = False,
    ) -> str:
        binding = {"HostPort": ""}
        if loopback and Docker.host() == "127.0.0.1":
            binding["HostIp"] = "127.0.0.1"
        spec: dict[str, Any] = {
            "Image": image,
            "Env": env,
            "Labels": labels,
            "ExposedPorts": {f"{port}/tcp": {}},
            "HostConfig": {"PortBindings": {f"{port}/tcp": [binding]}},
        }
        if command:
            spec["Cmd"] = command
        path = "/containers/create" + ("" if name is None else f"?name={quote(name)}")
        try:
            created = self._call("POST", path, spec)
        except DockerError as e:
            if e.status != 404:
                raise
            self._pull(image)
            created = self._call("POST", path, spec)
        self.start(created["Id"])
        return str(created["Id"])

    def start(self, id: str) -> None:
        self._call("POST", f"/containers/{id}/start")

    def remove(self, id: str) -> None:
        self._call("DELETE", f"/containers/{id}?force=1&v=1")

    def port(self, id: str, port: int) -> int:
        for _ in range(50):
            ports = self._call("GET", f"/containers/{id}/json")["NetworkSettings"]["Ports"] or {}
            bound = (ports.get(f"{port}/tcp") or [{}])[0].get("HostPort")
            if bound:
                return int(bound)
            time.sleep(0.1)
        raise DockerError(f"container {id} publishes no port for {port}")

    def _pull(self, image: str) -> None:
        name, _, tag = image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "latest")
        pulled = self._call("POST", f"/images/create?fromImage={quote(name)}&tag={quote(tag)}")
        if isinstance(pulled, dict) and "error" in pulled:
            raise DockerError(f"docker: {image} does not pull: {pulled['error']}")

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        connection = _Unix(self._socket)
        try:
            connection.request(
                method, path, None if body is None else json.dumps(body), {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            answer = response.read().decode("utf-8")
        except OSError as e:
            raise DockerError(f"docker at {self._socket}: {e}") from e
        finally:
            connection.close()
        if response.status >= 400:
            try:
                message = json.loads(answer).get("message", answer)
            except ValueError:
                message = answer
            raise DockerError(f"docker: {method} {path}: {message}", response.status)
        if path.startswith("/images/") and "\n" in answer.strip():
            answer = [line for line in answer.splitlines() if line.strip()][-1]
        try:
            return json.loads(answer) if answer.strip() else {}
        except ValueError:
            return {}


def _socket() -> str:
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host.removeprefix("unix://")
    home = Path.home()
    for candidate in (
        Path("/var/run/docker.sock"),
        home / ".docker/run/docker.sock",
        home / ".orbstack/run/docker.sock",
        home / ".colima/default/docker.sock",
        home / ".rd/docker.sock",
    ):
        if candidate.exists():
            return str(candidate)
    return "/var/run/docker.sock"
