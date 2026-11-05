from __future__ import annotations

import socket
import threading
from collections.abc import Callable


# The service reaches a resource through a gate of the run's own, so that a case can
# take the resource away from every client the service built, whenever it built it.
class Gate:
    def __init__(self, upstream: Callable[[], tuple[str, int]]) -> None:
        self._upstream = upstream
        self._target: tuple[str, int] | None = None
        self._lock = threading.Lock()
        self._resolving = threading.Lock()
        self._open: set[socket.socket] = set()
        self._listener: socket.socket | None = self._listen(0)
        self.port: int = self._listener.getsockname()[1]
        self.refusing = False
        threading.Thread(target=self._accept, args=(self._listener,), daemon=True).start()

    def refuse(self) -> None:
        with self._lock:
            if self.refusing:
                return
            self.refusing = True
            listener, self._listener = self._listener, None
            open_, self._open = self._open, set()
        if listener is not None:
            _drop(listener)
        for s in open_:
            _drop(s)

    def reopen(self) -> None:
        with self._lock:
            if not self.refusing:
                return
            self.refusing = False
            listener = self._listener = self._listen(self.port)
        threading.Thread(target=self._accept, args=(listener,), daemon=True).start()

    @staticmethod
    def _listen(port: int) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(64)
        return listener

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=self._pipe, args=(client,), daemon=True).start()

    def _pipe(self, client: socket.socket) -> None:
        try:
            with self._resolving:
                if self._target is None:
                    self._target = self._upstream()
            server = socket.create_connection(self._target)
        except Exception:
            _drop(client)
            return
        with self._lock:
            if self.refusing:
                _drop(client)
                _drop(server)
                return
            self._open.update((client, server))
        threading.Thread(target=self._copy, args=(client, server), daemon=True).start()
        threading.Thread(target=self._copy, args=(server, client), daemon=True).start()

    def _copy(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            pass
        finally:
            with self._lock:
                self._open.discard(source)
                self._open.discard(sink)
            _drop(source)
            _drop(sink)


def _drop(s: socket.socket) -> None:
    try:
        s.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    s.close()
