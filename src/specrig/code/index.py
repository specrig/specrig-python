from __future__ import annotations

import ast
import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

VARYING = "{}"
# A parameter of a function nobody calls carries no value at all.
UNCALLED = "\0"
DEEPEST = 12

Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


@dataclass
class Module:
    name: str
    file: Path
    tree: ast.Module
    package: str
    imports: dict[str, str] = field(default_factory=dict)


class Code:
    def __init__(self, root: Path, files: list[Path]) -> None:
        self.root = root
        self.modules: dict[str, Module] = {}
        self.parents: dict[ast.AST, ast.AST] = {}
        self.owner: dict[ast.AST, Module] = {}
        self.definitions: dict[str, ast.AST] = {}
        self._names: dict[ast.AST, str] = {}
        self.bases: dict[str, list[str]] = {}
        self.calls: dict[str, list[ast.Call]] = {}
        self._templates: dict[ast.AST, list[str]] = {}
        self._types: dict[ast.AST, list[str]] = {}
        self._bindings: dict[ast.AST, dict[str, list[ast.expr]]] = {}
        for file in files:
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            except SyntaxError as e:
                raise ValueError(f"{file}: {e}") from e
            name, package = _module_name(root, file)
            module = Module(name, file, tree, package)
            self.modules[name] = module
            self.owner[tree] = module
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    self.parents[child] = node
        for module in self.modules.values():
            self._index(module)

    def _index(self, module: Module) -> None:
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        module.imports[alias.asname] = alias.name
                    else:
                        module.imports[alias.name.split(".")[0]] = alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                base = _absolute(module, node)
                for alias in node.names:
                    module.imports[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
            elif isinstance(node, ast.Call):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if callee:
                    self.calls.setdefault(callee, []).append(node)
        self._define(module.tree.body, module.name)

    def _define(self, body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.definitions[f"{prefix}.{stmt.name}"] = stmt
                self._names[stmt] = f"{prefix}.{stmt.name}"
            elif isinstance(stmt, ast.ClassDef):
                name = f"{prefix}.{stmt.name}"
                self.definitions[name] = stmt
                self._names[stmt] = name
                self.bases[name] = [b for b in (self.qualified(base) for base in stmt.bases) if b]
                self._define(stmt.body, name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        self.definitions[f"{prefix}.{target.id}"] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                self.definitions[f"{prefix}.{stmt.target.id}"] = stmt.value
            elif isinstance(stmt, (ast.If, ast.Try)):
                self._define([*stmt.body, *stmt.orelse], prefix)

    def module_of(self, node: ast.AST) -> Module:
        at = node
        while at not in self.owner:
            at = self.parents[at]
        return self.owner[at]

    def enclosing(self, node: ast.AST, *kinds: type) -> ast.AST | None:
        at = self.parents.get(node)
        while at is not None and not isinstance(at, kinds):
            at = self.parents.get(at)
        return at

    def scope(self, node: ast.AST) -> Scope | None:
        return cast(Scope | None, self.enclosing(node, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))

    def class_of(self, node: ast.AST) -> str | None:
        found = self.enclosing(node, ast.ClassDef)
        return None if found is None else self.name_of(found)

    def name_of(self, definition: ast.AST) -> str | None:
        return self._names.get(definition)

    def where(self, node: ast.AST) -> str:
        return self.class_of(node) or self.module_of(node).name

    def ancestry(self, name: str) -> list[str]:
        line: list[str] = []
        todo = [name]
        while todo and len(line) < DEEPEST:
            at = todo.pop(0)
            if at not in line:
                line.append(at)
                todo.extend(self.bases.get(at, []))
        return line

    def extends(self, name: str, ancestor: str) -> bool:
        return ancestor in self.ancestry(name)[1:]

    # The qualified name an expression refers to, as an import would reach it.
    def qualified(self, expr: ast.AST | None) -> str | None:
        if isinstance(expr, ast.Name):
            module = self.module_of(expr)
            if expr.id in module.imports:
                return module.imports[expr.id]
            klass = self.class_of(expr)
            for prefix in ([klass] if klass else []) + [module.name]:
                if f"{prefix}.{expr.id}" in self.definitions:
                    return f"{prefix}.{expr.id}"
            return expr.id
        if isinstance(expr, ast.Attribute):
            owner = self.qualified(expr.value)
            return None if owner is None else f"{owner}.{expr.attr}"
        return None

    def call_args(self, call: ast.Call) -> tuple[list[ast.expr], dict[str, ast.expr]]:
        return [a for a in call.args if not isinstance(a, ast.Starred)], {
            k.arg: k.value for k in call.keywords if k.arg
        }

    def keyword(self, call: ast.Call, name: str) -> ast.expr | None:
        return self.call_args(call)[1].get(name)

    def argument(self, call: ast.Call, position: int, name: str) -> ast.expr | None:
        positional, named = self.call_args(call)
        if name in named:
            return named[name]
        return positional[position] if position < len(positional) else None

    # Each expression is read once: a name assigned from itself — x = x.strip() — is a
    # cycle, and a visit inside it sees what varies.
    def templates(self, expr: ast.AST | None, depth: int = 0) -> list[str]:
        if expr is None or depth > DEEPEST:
            return [VARYING]
        if expr not in self._templates:
            self._templates[expr] = [VARYING]
            self._templates[expr] = self._template_of(expr, depth)
        return self._templates[expr]

    def _template_of(self, expr: ast.AST, depth: int) -> list[str]:
        if isinstance(expr, ast.Constant):
            return (
                [str(expr.value)]
                if isinstance(expr.value, (str, int, float)) and not isinstance(expr.value, bool)
                else [VARYING]
            )
        if isinstance(expr, ast.JoinedStr):
            sofar = [""]
            for part in expr.values:
                if isinstance(part, ast.Constant):
                    pieces = [str(part.value)]
                elif isinstance(part, ast.FormattedValue):
                    pieces = self.templates(part.value, depth + 1)
                else:
                    pieces = [VARYING]
                sofar = _product(sofar, pieces)
            return sofar
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return _product(self.templates(expr.left, depth + 1), self.templates(expr.right, depth + 1))
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mod) and isinstance(expr.left, ast.Constant):
            return [
                re.sub(
                    r"%%|%[-+ 0#]*\d*(?:\.\d+)?[a-zA-Z]",
                    lambda m: "%" if m.group(0) == "%%" else VARYING,
                    str(expr.left.value),
                )
            ]
        if isinstance(expr, ast.IfExp):
            return [*self.templates(expr.body, depth + 1), *self.templates(expr.orelse, depth + 1)]
        if isinstance(expr, ast.BoolOp):
            return [t for value in expr.values for t in self.templates(value, depth + 1)]
        if isinstance(expr, ast.Call):
            return self._called(expr, depth)
        if isinstance(expr, ast.Name):
            return self._name(expr, depth)
        if isinstance(expr, ast.Attribute):
            return self._attribute(expr, depth)
        return [VARYING]

    def _called(self, call: ast.Call, depth: int) -> list[str]:
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "format" and isinstance(func.value, ast.Constant):
            return [re.sub(r"\{[^{}]*\}", VARYING, str(func.value.value))]
        if isinstance(func, ast.Name) and func.id == "str" and call.args:
            return self.templates(call.args[0], depth + 1)
        if (
            isinstance(func, ast.Attribute)
            and func.attr in ("rstrip", "strip", "lstrip", "removesuffix")
            and not call.args
        ):
            return self.templates(func.value, depth + 1)
        return [VARYING]

    def _name(self, name: ast.Name, depth: int) -> list[str]:
        scope = self.scope(name)
        if scope is not None and not isinstance(scope, ast.Lambda):
            for position, param in enumerate(_params(scope)):
                if param.arg == name.id:
                    return self.parameter(scope, position, name.id, depth)
            found = [t for value in self.bound(scope, name.id) for t in self.templates(value, depth + 1)]
            if found:
                return list(dict.fromkeys(found))
        qualified = self.qualified(name)
        definition = self.definitions.get(qualified or "")
        if isinstance(definition, ast.expr):
            return self.templates(definition, depth + 1)
        return [VARYING]

    def _attribute(self, attribute: ast.Attribute, depth: int) -> list[str]:
        if isinstance(attribute.value, ast.Name) and attribute.value.id in ("self", "cls"):
            klass = self.class_of(attribute)
            found: list[str] = []
            for at in self.ancestry(klass) if klass else []:
                definition = self.definitions.get(f"{at}.{attribute.attr}")
                if isinstance(definition, ast.expr):
                    found += self.templates(definition, depth + 1)
                found += [
                    t for value in self._self_assigned(at, attribute.attr) for t in self.templates(value, depth + 1)
                ]
            return list(dict.fromkeys(found)) or [VARYING]
        definition = self.definitions.get(self.qualified(attribute) or "")
        if isinstance(definition, ast.expr):
            return self.templates(definition, depth + 1)
        return [VARYING]

    def _self_assigned(self, klass: str, attribute: str) -> Iterator[ast.expr]:
        definition = self.definitions.get(klass)
        if not isinstance(definition, ast.ClassDef):
            return
        for node in ast.walk(definition):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            value = getattr(node, "value", None)
            for target in targets:
                if (
                    value is not None
                    and isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == attribute
                ):
                    yield value

    def parameter(self, function: Scope, position: int, name: str, depth: int) -> list[str]:
        callers = self.callers(function)
        if not callers:
            return [UNCALLED]
        found: list[str] = []
        bound = 1 if self._method(function) else 0
        for call in callers:
            found += self.templates(self.argument(call, position - bound, name), depth + 1)
        return list(dict.fromkeys(found))

    def _method(self, function: Scope) -> bool:
        parent = self.parents.get(function)
        params = _params(function)
        return isinstance(parent, ast.ClassDef) and bool(params) and params[0].arg in ("self", "cls")

    def callers(self, function: Scope) -> list[ast.Call]:
        if isinstance(function, ast.Lambda):
            return []
        defined = self.name_of(function)
        klass = self.class_of(function)
        callers = []
        for call in self.calls.get(function.name, []):
            if isinstance(call.func, ast.Name):
                if self.qualified(call.func) == defined:
                    callers.append(call)
            elif isinstance(call.func, ast.Attribute) and klass is not None:
                receiver = call.func.value
                types = (
                    [self.class_of(call) or ""]
                    if isinstance(receiver, ast.Name) and receiver.id in ("self", "cls")
                    else self.types(receiver)
                )
                if any(t == klass or klass in self.ancestry(t) for t in types):
                    callers.append(call)
            elif isinstance(call.func, ast.Attribute) and self.qualified(call.func) == defined:
                callers.append(call)
        return callers

    # The classes an expression may hold, as far as the code says: what it was built
    # from, what it was declared as, what returned it.
    def types(self, expr: ast.AST | None, depth: int = 0) -> list[str]:
        if expr is None or depth > DEEPEST:
            return []
        if expr not in self._types:
            self._types[expr] = []
            self._types[expr] = list(dict.fromkeys(self._types_of(expr, depth)))
        return self._types[expr]

    def bound(self, scope: ast.AST, name: str) -> list[ast.expr]:
        if scope not in self._bindings:
            self._bindings[scope] = _bindings(scope)
        return self._bindings[scope].get(name, [])

    def _types_of(self, expr: ast.AST, depth: int) -> list[str]:
        if isinstance(expr, ast.Await):
            return self.types(expr.value, depth + 1)
        if isinstance(expr, ast.Call):
            callee = self.qualified(expr.func)
            definition = self.definitions.get(callee or "")
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return self._returns(definition, depth)
            if isinstance(expr.func, ast.Attribute) and not isinstance(definition, ast.ClassDef):
                owners = [
                    o
                    for o in self.types(expr.func.value, depth + 1)
                    if o not in self.modules and o not in _KNOWN_MODULES
                ]
                if owners:
                    return [t for owner in owners for t in self._method_returns(owner, expr.func.attr, depth)]
            return [callee] if callee else []
        if isinstance(expr, ast.Name):
            if expr.id in ("self", "cls"):
                klass = self.class_of(expr)
                return [klass] if klass else []
            scope = self.scope(expr)
            if scope is not None and not isinstance(scope, ast.Lambda):
                for param in _params(scope):
                    if param.arg == expr.id:
                        return self._annotated(param.annotation)
                found = [t for value in self.bound(scope, expr.id) for t in self.types(value, depth + 1)]
                if found:
                    return found
            qualified = self.qualified(expr)
            definition = self.definitions.get(qualified or "")
            if isinstance(definition, ast.expr):
                return self.types(definition, depth + 1)
            return [qualified] if qualified in self.modules or qualified in _KNOWN_MODULES else []
        if isinstance(expr, ast.Attribute):
            if isinstance(expr.value, ast.Name) and expr.value.id in ("self", "cls"):
                klass = self.class_of(expr)
                found = []
                for at in self.ancestry(klass) if klass else []:
                    found += self._attribute_types(at, expr.attr, depth)
                return found
            qualified = self.qualified(expr)
            definition = self.definitions.get(qualified or "")
            if isinstance(definition, ast.expr):
                return self.types(definition, depth + 1)
            return []
        return []

    def _attribute_types(self, klass: str, attribute: str, depth: int) -> list[str]:
        definition = self.definitions.get(klass)
        if not isinstance(definition, ast.ClassDef):
            return []
        found: list[str] = []
        for node in definition.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == attribute:
                found += self._annotated(node.annotation)
        for value in self._self_assigned(klass, attribute):
            found += self.types(value, depth + 1)
        return found

    def _method_returns(self, owner: str, method: str, depth: int) -> list[str]:
        for at in self.ancestry(owner):
            definition = self.definitions.get(f"{at}.{method}")
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return self._returns(definition, depth)
        return []

    def _returns(self, function: ast.FunctionDef | ast.AsyncFunctionDef, depth: int) -> list[str]:
        declared = self._annotated(function.returns)
        if declared:
            return declared
        return [
            t for node in ast.walk(function) if isinstance(node, ast.Return) for t in self.types(node.value, depth + 1)
        ]

    def _annotated(self, annotation: ast.AST | None) -> list[str]:
        if annotation is None:
            return []
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return []
            self.parents[parsed] = annotation
            return self._annotated(parsed)
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return [*self._annotated(annotation.left), *self._annotated(annotation.right)]
        if isinstance(annotation, ast.Subscript):
            return (
                [t for t in self._annotated(annotation.slice) if t]
                if self.qualified(annotation.value) in ("typing.Optional", "Optional", "typing.Union", "Union")
                else []
            )
        if isinstance(annotation, ast.Tuple):
            return [t for e in annotation.elts for t in self._annotated(e)]
        qualified = self.qualified(annotation)
        return [qualified] if qualified and qualified != "None" else []


_KNOWN_MODULES = {"httpx", "httpx2", "requests", "os"}


def _params(function: Scope) -> list[ast.arg]:
    args = function.args
    return [*args.posonlyargs, *args.args, *args.kwonlyargs]


def _bindings(scope: ast.AST) -> dict[str, list[ast.expr]]:
    found: dict[str, list[ast.expr]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    found.setdefault(item.optional_vars.id, []).append(item.context_expr)
    return found


def _product(left: list[str], right: list[str]) -> list[str]:
    return [a + b for a, b in itertools.product(left, right or [VARYING])]


def _module_name(root: Path, file: Path) -> tuple[str, str]:
    parts = list(file.relative_to(root).with_suffix("").parts)
    if parts and parts[0] == "src" and not (root / "src" / "__init__.py").is_file():
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
        return ".".join(parts), ".".join(parts)
    return ".".join(parts), ".".join(parts[:-1])


def _absolute(module: Module, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = module.package.split(".") if module.package else []
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])
