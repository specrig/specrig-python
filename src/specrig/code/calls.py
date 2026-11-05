from __future__ import annotations

import ast
import re

from ..surface import Surface
from .index import UNCALLED, VARYING, Code

_CLIENTS = {
    "httpx.Client",
    "httpx.AsyncClient",
    "httpx2.Client",
    "httpx2.AsyncClient",
    "requests.Session",
    "requests.sessions.Session",
}
_MODULES = {"httpx", "httpx2", "requests"}
_VERBS = {"get", "head", "post", "put", "patch", "delete", "options"}


def read(code: Code, surface: Surface) -> None:
    for module in code.modules.values():
        for call in ast.walk(module.tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            method = call.func.attr
            if method not in _VERBS and method not in ("request", "stream"):
                continue
            types = set(code.types(call.func.value))
            if not types & _CLIENTS and not types & _MODULES:
                continue
            if method in _VERBS:
                verbs, url = [method.upper()], code.argument(call, 0, "url")
            else:
                verbs = [
                    v.upper()
                    for v in code.templates(code.argument(call, 0, "method"))
                    if VARYING not in v and UNCALLED not in v
                ]
                url = code.argument(call, 1, "url")
            for template in code.templates(url):
                if template.startswith(UNCALLED):
                    continue
                for verb in verbs:
                    surface.call(code.where(call), f"{verb} {path(template.replace(UNCALLED, VARYING))}")


# A request's path as the record names it: what varies is {}, an address that starts
# with what varies is a base address and its path, and one with no constant part is
# from data, somewhere outside.
def path(template: str) -> str:
    route = template.split("?", 1)[0]
    absolute = re.match(r"^[a-z][a-z0-9+.-]*://[^/]*(/.*)?$", route, re.IGNORECASE)
    if absolute:
        route = absolute.group(1) or ""
    elif route.startswith(VARYING) and "/" in route:
        route = route[route.index("/") :]
    if route.replace("/", "") in (VARYING, ""):
        return "/" if route == "/" else "external"
    segments = [s for s in route.split("/") if s != ""]
    return "/" + "/".join(VARYING if VARYING in s else s for s in segments)
