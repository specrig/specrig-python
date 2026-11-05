from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ..http import route
from ..surface import Surface
from .index import UNCALLED, VARYING, Code

_APPS = {"fastapi.FastAPI", "fastapi.applications.FastAPI"}
_ROUTERS = {"fastapi.APIRouter", "fastapi.routing.APIRouter"}
_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


@dataclass(eq=False)
class _Router:
    app: bool
    prefix: str
    routes: list[tuple[list[str], str]] = field(default_factory=list)
    includes: list[tuple[_Router, str]] = field(default_factory=list)
    included: bool = False


def read(code: Code, surface: Surface) -> None:
    routers: dict[ast.Call, _Router] = {}
    for module in code.modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                kind = code.qualified(node.func)
                if kind in _APPS or kind in _ROUTERS:
                    prefix = code.keyword(node, "prefix")
                    routers[node] = _Router(kind in _APPS, "" if prefix is None else _constant(code, prefix, node))
    for module in code.modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        _route(code, routers, decorator, decorator.func)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = _router(code, routers, node.func.value)
                if owner is None:
                    continue
                if node.func.attr == "add_api_route":
                    methods = _methods(code, code.keyword(node, "methods")) or ["GET"]
                    owner.routes.append((methods, _constant(code, code.argument(node, 0, "path"), node)))
                elif node.func.attr in ("include_router", "mount"):
                    child = _router(
                        code,
                        routers,
                        code.argument(
                            node,
                            0 if node.func.attr == "include_router" else 1,
                            "router" if node.func.attr == "include_router" else "app",
                        ),
                    )
                    if child is not None:
                        at = (
                            code.keyword(node, "prefix")
                            if node.func.attr == "include_router"
                            else code.argument(node, 0, "path")
                        )
                        child.included = True
                        owner.includes.append((child, "" if at is None else _constant(code, at, node)))
    for root in routers.values():
        if root.app or not root.included:
            _serve(root, "", surface, set())


def _route(code: Code, routers: dict[ast.Call, _Router], decorator: ast.Call, func: ast.Attribute) -> None:
    owner = _router(code, routers, func.value)
    if owner is None:
        return
    if func.attr in _VERBS:
        owner.routes.append(([func.attr.upper()], _constant(code, code.argument(decorator, 0, "path"), decorator)))
    elif func.attr == "api_route":
        methods = _methods(code, code.keyword(decorator, "methods")) or ["GET"]
        owner.routes.append((methods, _constant(code, code.argument(decorator, 0, "path"), decorator)))


def _router(code: Code, routers: dict[ast.Call, _Router], expr: ast.AST | None) -> _Router | None:
    for value in _values(code, expr):
        if value in routers:
            return routers[value]
    return None


def _values(code: Code, expr: ast.AST | None) -> list[ast.AST]:
    if isinstance(expr, ast.Call):
        return [expr]
    if isinstance(expr, ast.Name):
        scope = code.scope(expr)
        if scope is not None and not isinstance(scope, ast.Lambda):
            local = [
                n.value
                for n in ast.walk(scope)
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == expr.id for t in n.targets)
            ]
            if local:
                return local
    definition = code.definitions.get(code.qualified(expr) or "")
    return [definition] if definition is not None else []


def _methods(code: Code, expr: ast.AST | None) -> list[str]:
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        return [m.upper() for e in expr.elts for m in code.templates(e) if VARYING not in m and UNCALLED not in m]
    return []


def _constant(code: Code, expr: ast.AST | None, at: ast.AST) -> str:
    found = code.templates(expr)
    if len(found) != 1 or VARYING in found[0] or UNCALLED in found[0]:
        file = code.module_of(at).file.relative_to(code.root)
        raise ValueError(
            f"{file}:{getattr(at, 'lineno', 0)}: serves a route that is not a constant — it is a contract, give it one"
        )
    return found[0]


def _serve(router: _Router, prefix: str, surface: Surface, seen: set[int]) -> None:
    if id(router) in seen:
        return
    seen = {*seen, id(router)}
    under = prefix + router.prefix
    for methods, path in router.routes:
        for method in methods:
            surface.serves.add(f"{method} {route(under + path, lambda name, kind: kind == 'path')}")
    for child, at in router.includes:
        _serve(child, under + at, surface, seen)
