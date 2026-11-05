from __future__ import annotations

from collections.abc import Callable

_downs: list[Callable[[], None]] = []


def ending(down: Callable[[], None]) -> None:
    _downs.append(down)


def end() -> None:
    downs = list(reversed(_downs))
    _downs.clear()
    failures = []
    for down in downs:
        try:
            down()
        except Exception as e:
            failures.append(str(e))
    if failures:
        raise RuntimeError(f"the run's resources did not all come down: {'; '.join(failures)}")
