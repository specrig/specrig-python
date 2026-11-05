from __future__ import annotations

import contextlib
import inspect
import io
import re
from collections.abc import Callable
from typing import Any, ClassVar
from urllib.parse import unquote_to_bytes, urlsplit

from ..http import Client, Request, Response, exchange, route
from ..resource import Databases
from ..rig import Rig, resolve, runnable


class Harness(Rig):
    settings: ClassVar[str]

    def __init__(self) -> None:
        super().__init__()
        self._application: Any = None
        self._databases = _Databases()

    @classmethod
    def _framework(cls) -> dict[str, str | None]:
        return {"DJANGO_SETTINGS_MODULE": cls.settings}

    def databases(self) -> Databases:
        return self._databases

    def http(self) -> Client:
        return Client(lambda request: exchange(self.trace, request, self._handle))

    def run(self, job: Any, *args: Any, **kwargs: Any) -> Any:
        target = resolve(job) if isinstance(job, str) else job
        return self.traced("command", target.__name__, None, lambda: _called(runnable(target), *args, **kwargs))

    def command(self, name: str, *args: str) -> int:
        def call() -> int:
            from django.core.management import CommandError, call_command

            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    call_command(name, *args, stdout=out, stderr=out)
                return 0
            except CommandError as e:
                out.write(f"CommandError: {e}\n")
                return e.returncode
            except SystemExit as e:
                return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            finally:
                self.output = out.getvalue()

        return self.traced("command", name, list(args) or None, call, exits=True)

    # Django's app registry is filled once per process, before a model is imported:
    # the steps import them. As Django's own test runner does, the harness's host is
    # let in beside the service's.
    @classmethod
    def _prepared(cls) -> None:
        import django
        from django.conf import settings

        django.setup()
        if "testserver" not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]

    def _first(self) -> None:
        pass

    def _start(self) -> None:
        from django.core.cache import caches
        from django.core.servers.basehttp import get_internal_wsgi_application
        from django.db import connections

        connections.close_all()
        for cache in caches.all(initialized_only=True):
            cache.clear()
        self._application = get_internal_wsgi_application()

    def _stop(self) -> None:
        from django.core.cache import caches
        from django.db import connections

        self._application = None
        connections.close_all()
        for cache in caches.all(initialized_only=True):
            cache.close()

    def _handle(self, request: Request) -> tuple[Response, str | None]:
        self.boot()
        parts = urlsplit(request.url)
        path = unquote_to_bytes(parts.path or "/").decode("iso-8859-1")
        environ: dict[str, Any] = {
            "REQUEST_METHOD": request.method,
            "SCRIPT_NAME": "",
            "PATH_INFO": path,
            "QUERY_STRING": parts.query,
            "SERVER_NAME": parts.hostname or "testserver",
            "SERVER_PORT": str(parts.port or (443 if parts.scheme == "https" else 80)),
            "SERVER_PROTOCOL": "HTTP/1.1",
            "REMOTE_ADDR": self.address,
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": parts.scheme or "http",
            "wsgi.input": io.BytesIO(request.body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "CONTENT_LENGTH": str(len(request.body)),
            "HTTP_HOST": parts.netloc or "testserver",
        }
        for name, value in request.headers.items():
            key = name.upper().replace("-", "_")
            environ[key if key in ("CONTENT_TYPE", "CONTENT_LENGTH") else f"HTTP_{key}"] = value
        started: list[Any] = []

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Callable[..., Any]:
            started[:] = [status, headers]
            return lambda data: None

        answer = self._application(environ, start_response)
        try:
            body = b"".join(answer)
        finally:
            if hasattr(answer, "close"):
                answer.close()
        status, headers = started
        return Response(int(status.split(" ", 1)[0]), dict(headers), body), _named(path)


def _named(path: str) -> str | None:
    from django.urls import Resolver404
    from django.urls import resolve as resolved

    try:
        template = resolved(path).route
    except Resolver404:
        return None
    if template.startswith("^") or "(?P<" in template:
        template = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", template)
        template = template.replace("^", "").replace("\\Z", "").replace("$", "")
    template = re.sub(
        r"<(?:(\w+):)?(\w+)>", lambda m: "{" + m.group(2) + (":path}" if m.group(1) == "path" else "}"), template
    )
    return route(template, lambda name, kind: kind == "path")


def _called(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        from asgiref.sync import async_to_sync

        return async_to_sync(fn)(*args, **kwargs)
    return fn(*args, **kwargs)


class _Databases(Databases):
    def __init__(self) -> None:
        self._wrapped: list[tuple[Any, Callable[..., Any]]] = []

    def migrate(self) -> None:
        from django.core.management import call_command

        call_command("migrate", interactive=False, verbosity=0)

    def bookkeeping(self) -> list[str]:
        return ["django_migrations", "django_content_type", "auth_permission"]

    def fail(self, database: str, error: BaseException) -> None:
        from django.db import connections

        def failing(execute: Callable[..., Any], sql: str, params: Any, many: bool, context: Any) -> Any:
            raise error

        for connection in connections.all():
            if connection.settings_dict.get("NAME") == database:
                connection.execute_wrappers.append(failing)
                self._wrapped.append((connection, failing))

    def heal(self) -> None:
        from django.db import connections

        for connection, wrapper in self._wrapped:
            if wrapper in connection.execute_wrappers:
                connection.execute_wrappers.remove(wrapper)
        self._wrapped = []
        connections.close_all()
