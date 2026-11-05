from __future__ import annotations

import ast

from ..surface import Surface
from .index import UNCALLED, VARYING, Code

_SETTINGS = ("pydantic_settings.BaseSettings", "pydantic_settings.main.BaseSettings", "pydantic.BaseSettings")


def read(code: Code, surface: Surface) -> None:
    for module in code.modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Subscript) and _environ(code, node.value) and isinstance(node.ctx, ast.Load):
                _add(code, node.slice, surface.settings)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                _call(code, node, surface)
            elif isinstance(node, ast.Call) and code.qualified(node.func) == "os.getenv":
                _add(code, code.argument(node, 0, "key"), surface.optional)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and _django_environ(code, node.func):
                _env(code, node, surface)
            elif isinstance(node, ast.ClassDef):
                _pydantic(code, node, surface)


def _environ(code: Code, expr: ast.AST) -> bool:
    return code.qualified(expr) in ("os.environ", "environ")


def _call(code: Code, call: ast.Call, surface: Surface) -> None:
    assert isinstance(call.func, ast.Attribute)
    if _environ(code, call.func.value) and call.func.attr == "get":
        _add(code, code.argument(call, 0, "key"), surface.optional)
    elif call.func.attr == "getenv" and code.qualified(call.func.value) == "os":
        _add(code, code.argument(call, 0, "key"), surface.optional)
    elif _django_environ(code, call.func.value):
        _env(code, call, surface)


def _django_environ(code: Code, expr: ast.AST) -> bool:
    return bool({"environ.Env", "environ.environ.Env"} & set(code.types(expr)))


def _env(code: Code, call: ast.Call, surface: Surface) -> None:
    _, named = code.call_args(call)
    _add(code, code.argument(call, 0, "var"), surface.optional if "default" in named else surface.settings)


def _add(code: Code, expr: ast.AST | None, into: set[str]) -> None:
    for name in code.templates(expr):
        if VARYING not in name and UNCALLED not in name and name:
            into.add(name)


def _pydantic(code: Code, klass: ast.ClassDef, surface: Surface) -> None:
    name = code.name_of(klass)
    if name is None or not any(code.extends(name, base) for base in _SETTINGS):
        return
    prefix = ""
    for at in reversed(code.ancestry(name)):
        definition = code.definitions.get(at)
        if not isinstance(definition, ast.ClassDef):
            continue
        for stmt in definition.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
            ):
                prefix = _prefix(code, stmt.value) or prefix
    for at in reversed(code.ancestry(name)):
        definition = code.definitions.get(at)
        if not isinstance(definition, ast.ClassDef):
            continue
        for stmt in definition.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            field = stmt.target.id
            if field == "model_config" or field.startswith("_") or _class_var(code, stmt.annotation):
                continue
            alias = _alias(code, stmt.value)
            variable = alias or f"{prefix}{field}".upper()
            (surface.optional if _defaulted(code, stmt.value) else surface.settings).add(variable)


def _prefix(code: Code, config: ast.expr) -> str | None:
    if isinstance(config, ast.Call):
        found = code.keyword(config, "env_prefix")
        if found is not None:
            templates = code.templates(found)
            return templates[0] if templates and VARYING not in templates[0] else None
    if isinstance(config, ast.Dict):
        for key, value in zip(config.keys, config.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "env_prefix":
                return code.templates(value)[0]
    return None


def _class_var(code: Code, annotation: ast.expr) -> bool:
    target = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    return (code.qualified(target) or "").endswith("ClassVar")


def _field(code: Code, value: ast.expr | None) -> ast.Call | None:
    if isinstance(value, ast.Call) and (code.qualified(value.func) or "").endswith("Field"):
        return value
    return None


def _defaulted(code: Code, value: ast.expr | None) -> bool:
    if value is None:
        return False
    field = _field(code, value)
    if field is None:
        return True
    positional, named = code.call_args(field)
    return (
        bool(positional)
        and not (isinstance(positional[0], ast.Constant) and positional[0].value is Ellipsis)
        or ("default" in named or "default_factory" in named)
    )


def _alias(code: Code, value: ast.expr | None) -> str | None:
    field = _field(code, value)
    if field is None:
        return None
    for key in ("validation_alias", "alias"):
        found = code.keyword(field, key)
        if found is not None:
            templates = code.templates(found)
            if templates and VARYING not in templates[0]:
                return templates[0].upper()
    return None
