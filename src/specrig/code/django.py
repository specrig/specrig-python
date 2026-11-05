from __future__ import annotations

import ast
import re

from ..http import route
from ..surface import Surface
from .index import UNCALLED, VARYING, Code

_PATHS = {
    "django.urls.path",
    "django.urls.re_path",
    "django.conf.urls.url",
    "django.urls.conf.path",
    "django.urls.conf.re_path",
}
_INCLUDE = {"django.urls.include", "django.conf.urls.include", "django.urls.conf.include"}
_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
_DECORATED = {
    "django.views.decorators.http.require_GET": ["GET"],
    "django.views.decorators.http.require_POST": ["POST"],
    "django.views.decorators.http.require_safe": ["GET", "HEAD"],
}
_LISTED = {"django.views.decorators.http.require_http_methods", "rest_framework.decorators.api_view"}


def read(code: Code, surface: Surface, settings: str) -> None:
    urlconf = code.definitions.get(f"{settings}.ROOT_URLCONF")
    if urlconf is None:
        raise ValueError(f"{settings} sets no ROOT_URLCONF the code names")
    _module(code, _single(code, urlconf), "", surface, set())


def _single(code: Code, expr: ast.AST) -> str:
    found = code.templates(expr)
    if len(found) != 1 or VARYING in found[0] or UNCALLED in found[0]:
        raise ValueError("a URLconf named by what is not a constant")
    return found[0]


def _module(code: Code, name: str, prefix: str, surface: Surface, seen: set[str]) -> None:
    if name in seen or name not in code.modules:
        return
    seen = {*seen, name}
    patterns: list[ast.expr] = []
    for stmt in code.modules[name].tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "urlpatterns" for t in stmt.targets
        ):
            patterns = _listed(stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "urlpatterns":
            patterns += _listed(stmt.value)
    _patterns(code, patterns, prefix, surface, seen)


def _listed(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, (ast.List, ast.Tuple)):
        return list(expr.elts)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return [*_listed(expr.left), *_listed(expr.right)]
    return []


def _patterns(code: Code, patterns: list[ast.expr], prefix: str, surface: Surface, seen: set[str]) -> None:
    for pattern in patterns:
        if not isinstance(pattern, ast.Call) or code.qualified(pattern.func) not in _PATHS:
            continue
        at = prefix + _single(code, code.argument(pattern, 0, "route"))
        view = code.argument(pattern, 1, "view")
        if isinstance(view, ast.Call) and code.qualified(view.func) in _INCLUDE:
            included = code.argument(view, 0, "arg")
            if isinstance(included, ast.Tuple) and included.elts:
                included = included.elts[0]
            if isinstance(included, (ast.List, ast.Tuple)):
                _patterns(code, list(included.elts), at, surface, seen)
            elif isinstance(included, ast.Constant):
                _module(code, str(included.value), at, surface, seen)
            else:
                _module(code, code.qualified(included) or "", at, surface, seen)
            continue
        for method in _methods(code, view):
            surface.serves.add(f"{method} {_route(at)}")


# The view says which methods it answers; one that does not answers any, *.
def _methods(code: Code, view: ast.AST | None) -> list[str]:
    if isinstance(view, ast.Call) and isinstance(view.func, ast.Attribute) and view.func.attr == "as_view":
        klass = code.qualified(view.func.value) or ""
        defined = {
            verb
            for at in code.ancestry(klass)
            if isinstance(definition := code.definitions.get(at), ast.ClassDef)
            for verb in _VERBS
            if f"{at}.{verb}" in code.definitions
        }
        return sorted(v.upper() for v in defined) or ["*"]
    definition = code.definitions.get(code.qualified(view) or "")
    if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for decorator in definition.decorator_list:
            named = code.qualified(decorator.func if isinstance(decorator, ast.Call) else decorator)
            if named in _DECORATED:
                return _DECORATED[named]
            if named in _LISTED and isinstance(decorator, ast.Call):
                listed = code.argument(decorator, 0, "http_method_names")
                if isinstance(listed, (ast.List, ast.Tuple)):
                    return [m.upper() for e in listed.elts for m in code.templates(e) if VARYING not in m]
                return ["GET"]
    return ["*"]


def _route(template: str) -> str:
    if template.startswith("^") or "(?P<" in template:
        template = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", template)
        template = template.replace("^", "").replace("\\Z", "").replace("$", "")
    template = re.sub(
        r"<(?:(\w+):)?(\w+)>", lambda m: "{" + m.group(2) + (":path}" if m.group(1) == "path" else "}"), template
    )
    return route(template, lambda name, kind: kind == "path")
