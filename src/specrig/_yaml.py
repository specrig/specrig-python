from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_WHITE = "\x09-\x0d\x20\x85\xa0  -     　"
_CONTROL = "\x00-\x1f\x7f-\x9f"
_WORDS = re.compile(r"^(?:true|false|yes|no|on|off|y|n|null|~)$", re.IGNORECASE)
_NUMBER = re.compile(r"^[-+]?(\.?[0-9]|0x|0o|\.inf|\.nan)", re.IGNORECASE)
_EDGES = re.compile(f"^[{_WHITE}]|[{_WHITE}]$")
_CONTROLS = re.compile(f"[{_CONTROL}]")


# A mapping is a dict, a sequence a list; an entry whose value is None or empty is
# left out, as omitempty does in the Go record.
def emit(root: dict[str, Any]) -> str:
    return _write(root, 0)


def _write(mapping: dict[str, Any], indent: int) -> str:
    pad = " " * indent
    text = ""
    for key, value in mapping.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, str):
            text += f"{pad}{key}: {_scalar(value, indent + 2)}\n"
        elif isinstance(value, dict):
            text += f"{pad}{key}:\n{_write(value, indent + 2)}"
        elif isinstance(value, list) and isinstance(value[0], str):
            text += f"{pad}{key}:\n"
            for item in value:
                text += f"{pad}  - {_scalar(item, indent + 4)}\n"
        elif isinstance(value, list) and isinstance(value[0], dict):
            text += f"{pad}{key}:\n"
            for item in value:
                text += f"{pad}  - {_write(item, indent + 4)[indent + 4 :]}"
        else:
            raise TypeError(f"{key}: no YAML for a {type(value).__name__}")
    return text


def _scalar(value: str, indent: int) -> str:
    if _plain(value):
        return value
    if "\n" not in value or "\r" in value or value.startswith(" ") or value.endswith("\n\n"):
        return _quoted(value)
    clip = value.endswith("\n")
    pad = " " * indent
    text = "|" if clip else "|-"
    for line in (value[:-1] if clip else value).split("\n"):
        text += "\n" if line == "" else f"\n{pad}{line}"
    return text


def _plain(value: str) -> bool:
    return (
        value != ""
        and not _EDGES.search(value)
        and value[0] not in "-?:,[]{}#&*!|>'\"%@`"
        and ": " not in value
        and " #" not in value
        and not value.endswith(":")
        and not _CONTROLS.search(value)
        and not _WORDS.match(value)
        and not _NUMBER.match(value)
    )


def _quoted(value: str) -> str:
    text = '"'
    for c in value:
        if c == '"':
            text += '\\"'
        elif c == "\\":
            text += "\\\\"
        elif c == "\n":
            text += "\\n"
        elif c == "\t":
            text += "\\t"
        elif c == "\r":
            text += "\\r"
        elif _CONTROLS.match(c):
            text += f"\\x{ord(c):02x}"
        else:
            text += c
    return text + '"'


class _Loader(yaml.SafeLoader):
    pass


# YAML 1.2's core schema, as Go and Symfony read it: yes and no are words, and a
# date is the text it is written as.
_Loader.yaml_implicit_resolvers = {
    first: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


def read(path: Path) -> Any:
    return load(path.read_text(encoding="utf-8")) if path.is_file() else None
