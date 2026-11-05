from __future__ import annotations

from pathlib import Path

from ..surface import Surface
from ..tool.service import Service
from . import calls, django, fastapi, settings
from .index import Code

_SKIPPED = {"tests", "scripts", "venv", "env", "node_modules", "__pycache__", "build", "dist", "site-packages"}


def read(service: Service, name: str) -> Surface:
    code = Code(service.root, files(service.root))
    surface = Surface(name)
    calls.read(code, surface)
    settings.read(code, surface)
    if service.framework() == "django":
        django.read(code, surface, service.django_settings())
    else:
        fastapi.read(code, surface)
    return surface


def files(root: Path) -> list[Path]:
    found = []
    for file in root.rglob("*.py"):
        parts = file.relative_to(root).parts[:-1]
        if file.name != "conftest.py" and not any(part in _SKIPPED or part.startswith(".") for part in parts):
            found.append(file)
    return sorted(found)
