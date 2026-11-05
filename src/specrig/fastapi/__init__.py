from __future__ import annotations

import contextlib
import functools
import inspect
import io
import os
import subprocess
import sys
import tomllib
import weakref
from collections.abc import Callable
from typing import Any, ClassVar

from ..asgi import Server
from ..http import Client, Request, Response, exchange, route
from ..resource import Databases
from ..rig import Rig, resolve, runnable


class _Recorder:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.route: str | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        self.route = None
        try:
            await self.app(scope, receive, send)
        finally:
            self.route = _named(scope)


# A mount's prefix is in root_path as it matched; FastAPI's routers carry theirs in the
# route, and since 0.137 an included router's only in the context it keeps for the route.
def _named(scope: dict[str, Any]) -> str | None:
    matched = scope.get("route")
    effective = scope.get("fastapi", {}).get("effective_route_context")
    is_matched = getattr(effective, "original_route", None) is matched
    template = getattr(effective if is_matched else matched, "path", None)
    if not isinstance(template, str):
        return None
    root_path = scope.get("root_path", "")
    prefix = root_path[len(scope.get("app_root_path", "")) :]
    return route(prefix + template, lambda name, kind: kind == "path")


class Harness(Rig):
    app: ClassVar[str]
    console: ClassVar[str | None] = None

    def __init__(self) -> None:
        super().__init__()
        self._server: Server | None = None
        self._recorder: _Recorder | None = None
        self._overridden: list[tuple[Any, Any]] = []
        self._databases = _Databases(self)

    @property
    def application(self) -> Any:
        self.boot()
        assert self._recorder is not None
        return self._recorder.app

    def databases(self) -> Databases:
        return self._databases

    def http(self) -> Client:
        return Client(lambda request: exchange(self.trace, request, self._handle))

    def run(self, job: Any, *args: Any, **kwargs: Any) -> Any:
        target = resolve(job) if isinstance(job, str) else job
        return self.traced("command", target.__name__, None, lambda: self._call(runnable(target), *args, **kwargs))

    def command(self, name: str, *args: str) -> int:
        def call() -> int:
            console = self._console()
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                code = _exit(console, [name, *args])
            self.output = out.getvalue()
            return code

        return self.traced("command", name, list(args) or None, call, exits=True)

    def _first(self) -> None:
        resolve(self.app)

    def _start(self) -> None:
        target = resolve(self.app)
        app = target() if inspect.isfunction(target) else target
        overrides = getattr(app, "dependency_overrides", None)
        if overrides is not None:
            for original, instance in self._granted().values():
                self._overridden.append((original, overrides.get(original)))
                overrides[original] = functools.partial(_given, instance)
        self._recorder = _Recorder(app)
        self._server = Server(self._recorder)
        self._server.start()

    def _stop(self) -> None:
        server, self._server = self._server, None
        try:
            if server is not None:
                server.stop()
        finally:
            overrides = getattr(self._recorder.app if self._recorder else None, "dependency_overrides", None)
            for original, was in reversed(self._overridden):
                if overrides is None:
                    break
                if was is None:
                    overrides.pop(original, None)
                else:
                    overrides[original] = was
            self._overridden = []

    def _handle(self, request: Request) -> tuple[Response, str | None]:
        self.boot()
        assert self._server is not None and self._recorder is not None
        status, headers, body = self._server.request(
            request.method, request.url, request.headers, request.body, self.address
        )
        return Response(status, dict(headers), body), self._recorder.route

    # A coroutine runs on the loop the service's lifespan built its clients on.
    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        assert self._server is not None
        if inspect.iscoroutinefunction(fn):
            return self._server.portal.call(functools.partial(fn, *args, **kwargs))
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            awaitable = result

            async def awaited() -> Any:
                return await awaitable

            return self._server.portal.call(awaited)
        return result

    def _console(self) -> Any:
        if self.console is not None:
            return resolve(self.console)
        pyproject = self.root() / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}) if pyproject.is_file() else {}
        scripts = project.get("scripts", {})
        if len(scripts) != 1:
            raise RuntimeError(
                f"{type(self).__name__} names no console and pyproject.toml has "
                f"{len(scripts)} scripts: set console = 'module:callable'"
            )
        return resolve(next(iter(scripts.values())))


def _given(instance: Any) -> Any:
    return instance


def _exit(console: Any, argv: list[str]) -> int:
    command = console
    if type(console).__name__ == "Typer":
        import typer.main

        command = typer.main.get_command(console)
    try:
        if hasattr(command, "main") and hasattr(command, "params"):
            import click

            try:
                result = command.main(argv, standalone_mode=False)
            except click.exceptions.Exit as e:
                return e.exit_code
            except click.ClickException as e:
                e.show()
                return e.exit_code
            return result if isinstance(result, int) else 0
        result = command(argv)
        return result if isinstance(result, int) else 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)


class _Databases(Databases):
    def __init__(self, harness: Harness) -> None:
        self._harness = harness
        self._failing: Callable[..., None] | None = None

    # Alembic configures logging as it runs, so it runs as it does in production: a
    # process of its own.
    def migrate(self) -> None:
        root = self._harness.root()
        if not (root / "alembic.ini").is_file():
            return
        done = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            raise RuntimeError(f"alembic upgrade head: {(done.stderr or done.stdout).strip()}")

    def bookkeeping(self) -> list[str]:
        return ["alembic_version"]

    def fail(self, database: str, error: BaseException) -> None:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        self.heal()

        def failing(conn: Any, *args: Any) -> None:
            if conn.engine.url.database == database:
                raise error

        self._failing = failing
        event.listen(Engine, "before_cursor_execute", failing)

    def heal(self) -> None:
        try:
            from sqlalchemy import event
            from sqlalchemy.engine import Engine
        except ImportError:
            return
        if self._failing is not None:
            event.remove(Engine, "before_cursor_execute", self._failing)
            self._failing = None
        # The gate cut what the pools held, and a pool may belong to another loop.
        for engine in list(_engines):
            engine.dispose(close=False)


_engines: weakref.WeakSet[Any] = weakref.WeakSet()


def _track() -> None:
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
    except ImportError:
        return
    event.listen(Engine, "engine_connect", lambda connection: _engines.add(connection.engine))


_track()
