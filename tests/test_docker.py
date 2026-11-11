from typing import Any

import pytest

from specrig import mysql, postgres
from specrig.docker import Docker


def published(monkeypatch: pytest.MonkeyPatch, server: Any) -> list[dict[str, str]]:
    created: list[dict[str, Any]] = []

    def call(docker: Docker, method: str, path: str, body: Any = None) -> Any:
        if path.startswith("/containers/json"):
            return []
        if path.startswith("/containers/create"):
            created.append(body)
            return {"Id": "c1"}
        if path == "/containers/c1/json":
            return {"NetworkSettings": {"Ports": {f"{server.port}/tcp": [{"HostPort": "40000"}]}}}
        return {}

    monkeypatch.setattr(Docker, "_call", call)
    server._container()
    return created[0]["HostConfig"]["PortBindings"][f"{server.port}/tcp"]


@pytest.mark.parametrize("kind", [postgres._Server, mysql._Server])
def test_a_database_container_is_published_on_loopback(monkeypatch: pytest.MonkeyPatch, kind: Any) -> None:
    monkeypatch.delenv("TESTCONTAINERS_HOST_OVERRIDE", raising=False)

    assert published(monkeypatch, kind("loopback", None, "image:1", kind.scheme)) == [
        {"HostIp": "127.0.0.1", "HostPort": ""}
    ]


def test_a_suite_in_a_container_has_the_database_published_on_every_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTCONTAINERS_HOST_OVERRIDE", "host.docker.internal")

    assert published(monkeypatch, postgres._Server("everywhere", None, "image:1", "postgresql")) == [{"HostPort": ""}]
