from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .rig import Rig


class Resource(ABC):
    setting: str

    @abstractmethod
    def fresh(self) -> Resource: ...

    # What the service is answered with, known before anything starts: the
    # environment is set before the service's modules are imported.
    @abstractmethod
    def bind(self, service: str) -> str: ...

    @abstractmethod
    def provision(self) -> None: ...

    # Before the service starts: a service's start may read its database.
    @abstractmethod
    def ready(self, rig: Rig) -> None: ...

    @abstractmethod
    def started(self) -> None: ...

    def failures(self) -> list[str]:
        return []

    @abstractmethod
    def release(self) -> None: ...


class Databases(ABC):
    @abstractmethod
    def migrate(self) -> None: ...

    @abstractmethod
    def bookkeeping(self) -> list[str]: ...

    @abstractmethod
    def fail(self, database: str, error: BaseException) -> None: ...

    @abstractmethod
    def heal(self) -> None: ...
