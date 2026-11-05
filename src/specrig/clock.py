from __future__ import annotations

import datetime as dt

import time_machine


class Clock:
    def __init__(self) -> None:
        self._stopped: dt.datetime | None = None
        self._travel: time_machine.travel | None = None

    def now(self) -> dt.datetime:
        return self._stopped or dt.datetime.now(dt.UTC)

    def stopped(self) -> dt.datetime | None:
        return self._stopped

    def set(self, at: dt.datetime | dt.date | str) -> None:
        if isinstance(at, str):
            at = dt.datetime.fromisoformat(at)
        elif not isinstance(at, dt.datetime):
            at = dt.datetime.combine(at, dt.time())
        self._move(at if at.tzinfo else at.replace(tzinfo=dt.UTC))

    def advance(self, by: dt.timedelta) -> None:
        if self._stopped is None:
            raise RuntimeError("the clock follows the wall clock until a step sets it")
        self._move(self._stopped + by)

    def release(self) -> None:
        if self._travel is not None:
            self._travel.stop()
        self._travel = None
        self._stopped = None

    def _move(self, at: dt.datetime) -> None:
        self.release()
        self._travel = time_machine.travel(at, tick=False)
        self._travel.start()
        self._stopped = at
