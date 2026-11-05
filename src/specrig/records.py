from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Record:
    seq: int
    dir: str
    kind: str
    name: str
    step: str
    request: Any = None
    error: str | None = None


@dataclass(frozen=True)
class Run:
    case: str
    records: list[Record]


class Trace:
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._seq = 0
        self._step = ""
        self._lock = threading.Lock()

    def records(self) -> list[Record]:
        return list(self._records)

    # Context, Action or Outcome: what a Given, a When and a Then are for.
    def act(self, kind: str) -> None:
        self._step = kind

    def begin(self) -> tuple[int, str]:
        with self._lock:
            self._seq += 1
            return self._seq, self._step

    def end(
        self, call: tuple[int, str], dir: str, kind: str, name: str, request: Any = None, error: str | None = None
    ) -> None:
        with self._lock:
            self._records.append(Record(call[0], dir, kind, name, call[1], request, error))
