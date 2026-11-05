from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from . import _yaml


class Contract:
    def __init__(self, file: Path, neighbour: str, failure: str | None = None) -> None:
        doc = _yaml.read(file)
        if not isinstance(doc, dict):
            raise ValueError(f"{file} is not an OpenAPI document")
        self._neighbour = neighbour
        self._failure = failure
        self._paths: dict[str, Any] = doc.get("paths") or {}
        self._schemas: dict[str, Any] = {
            name: _nullable(schema) for name, schema in ((doc.get("components") or {}).get("schemas") or {}).items()
        }
        self._draft = (
            jsonschema.Draft4Validator
            if str(doc.get("openapi", "")).startswith("3.0")
            else jsonschema.Draft202012Validator
        )

    @staticmethod
    @cache
    def load(file: Path, neighbour: str, failure: str | None) -> Contract:
        return Contract(file, neighbour, failure)

    def _template(self, verb: str, path: str) -> str | None:
        wanted = path.split("/")
        best, most = None, -1
        for template, operations in self._paths.items():
            segments = str(template).split("/")
            if not isinstance(operations, dict) or verb not in operations or len(segments) != len(wanted):
                continue
            if any(not s.startswith("{") and s != wanted[i] for i, s in enumerate(segments)):
                continue
            literal = sum(1 for s in segments if not s.startswith("{"))
            if literal > most:
                best, most = str(template), literal
        return best

    # A request is named as the route that takes it, so that it meets the neighbour's input of the same name.
    def name(self, method: str, path: str) -> str:
        return f"{method} {self._template(method.lower(), path) or path}"

    def request(self, method: str, path: str, body: Any) -> list[str]:
        verb = method.lower()
        template = self._template(verb, path)
        if template is None:
            return [f"{method} {path}: {self._neighbour} serves no such route"]
        schema = _dig(self._paths[template][verb], "requestBody", "content", "application/json", "schema")
        if body is None or schema is None:
            return []
        return [f"{method} {path} request body: {e}" for e in self._validate(schema, body)]

    def response(self, method: str, path: str, status: int, body: Any) -> list[str]:
        verb = method.lower()
        template = self._template(verb, path)
        if template is None:
            return []
        where = f"{method} {path} answered {status}"
        responses = self._paths[template][verb].get("responses") or {}
        documented = responses.get(str(status), responses.get(status))
        if documented is None:
            if status >= 500 and self._failure is not None:
                if self._failure not in self._schemas:
                    raise ValueError(f"{self._neighbour} has no schema {self._failure}")
                return [
                    f"{where}: {e}" for e in self._validate({"$ref": f"#/components/schemas/{self._failure}"}, body)
                ]
            return [f"{where}: {self._neighbour} does not answer {status} here"]
        schema = _dig(documented, "content", "application/json", "schema")
        if schema is None:
            return []
        return [f"{where}: {e}" for e in self._validate(schema, body)]

    def _validate(self, schema: Any, body: Any) -> list[str]:
        root = {**_nullable(schema), "components": {"schemas": self._schemas}} if isinstance(schema, dict) else {}
        errors = sorted(self._draft(root).iter_errors(body), key=lambda e: list(e.absolute_path))
        return [f"{e.json_path} {e.message}" for e in errors[:20]]


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# OpenAPI 3.0's nullable is no JSON Schema keyword: it is a null among the types.
def _nullable(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_nullable(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    copy = {k: _nullable(v) for k, v in schema.items() if k != "nullable"}
    if schema.get("nullable") is True and isinstance(copy.get("type"), str):
        copy["type"] = [copy["type"], "null"]
    return copy
