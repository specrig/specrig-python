from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, TypeVar

from .resource import Databases, Resource

if TYPE_CHECKING:
    from .rig import Rig


class Write(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


class Engine(ABC):
    database: str
    address: str

    def __init__(self) -> None:
        self._migration: BaseException | bool | None = None

    @abstractmethod
    def start(self) -> None: ...

    def once(self, migrate: Callable[[], None], keep: Sequence[str]) -> None:
        if self._migration is None:
            try:
                migrate()
                self._remember(keep)
                self.reset(keep)
                self._migration = False
            except Exception as e:
                self._migration = e
        if isinstance(self._migration, BaseException):
            raise RuntimeError(f"{self.database} did not migrate: {self._migration}") from self._migration

    @abstractmethod
    def _remember(self, keep: Sequence[str]) -> None: ...

    @abstractmethod
    def reset(self, keep: Sequence[str]) -> None: ...

    @abstractmethod
    def refuse(self, table: str, rows: int, events: Sequence[str]) -> None: ...

    @abstractmethod
    def lift(self) -> None: ...

    @abstractmethod
    def down(self, databases: Databases) -> None: ...

    @abstractmethod
    def up(self) -> None: ...


E = TypeVar("E", bound=Engine)


class Database(Resource, Generic[E]):
    def __init__(self, setting: str, keep: Sequence[str]) -> None:
        self.setting = setting
        self._keep = tuple(keep)
        self._server: E | None = None
        self._rig: Rig | None = None
        self._started = False
        self._refusing = False
        self._error: BaseException | None = None
        self._refusals: list[Callable[[], None]] = []

    def bind(self, service: str) -> str:
        return self._of(service).address

    def provision(self) -> None:
        self._held().start()

    def ready(self, rig: Rig) -> None:
        databases = rig.databases()
        keep = (*self._keep, *databases.bookkeeping())
        server = self._held()
        server.once(databases.migrate, keep)
        server.reset(keep)
        self._rig = rig

    def started(self) -> None:
        self._started = True
        self._apply()

    # Refused, the database is down for every connection the service holds and every
    # one it makes; an error given instead fails every statement with it. A fault a
    # step sets before the boot waits for the service to start.
    def fail_with(self, error: BaseException | None = None) -> None:
        if error is None:
            self._refusing = True
        else:
            self._error = error
        if self._started:
            self._apply()

    def refuse(self, table: str, *writes: Write) -> None:
        self.refuse_after(table, 0, *writes)

    def refuse_after(self, table: str, rows: int, *writes: Write) -> None:
        events = trigger_events(writes)
        self._refuse(lambda: self._held().refuse(table, rows, events))

    def heal(self) -> None:
        self._calm()
        if self._started:
            self._held().lift()

    def release(self) -> None:
        self._calm()
        self._rig = None
        self._started = False

    def _calm(self) -> None:
        if self._server is not None:
            self._server.up()
        if self._rig is not None and (self._refusing or self._error is not None):
            self._rig.databases().heal()
        self._refusing = False
        self._error = None
        self._refusals = []

    def _refuse(self, refusal: Callable[[], None]) -> None:
        # Before the start the schema may not exist, and the reset would lift it.
        if self._started:
            refusal()
        else:
            self._refusals.append(refusal)

    def _apply(self) -> None:
        assert self._rig is not None
        if self._refusing:
            self._held().down(self._rig.databases())
        if self._error is not None:
            self._rig.databases().fail(self._held().database, self._error)
        refusals, self._refusals = self._refusals, []
        for refusal in refusals:
            refusal()

    @abstractmethod
    def _of(self, service: str) -> E: ...

    def _held(self) -> E:
        if self._server is None:
            raise RuntimeError(
                f"{type(self).__name__.lower()} is not provisioned: the harness has not prepared the run"
            )
        return self._server


def trigger_events(writes: Sequence[str]) -> list[str]:
    given = []
    for write in writes:
        try:
            given.append(Write(write))
        except ValueError:
            raise ValueError(f"specrig: {write!r} is not a write, refuse an insert, update or delete") from None
    return [write.value for write in Write if not given or write in given]
