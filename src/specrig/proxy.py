from __future__ import annotations

import socketserver
import threading

_VARIABLES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
_REFUSAL = b"specrig: no neighbour the harness names is here\n"


class _Refuse(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        self.connection.settimeout(5)
        try:
            line = self.rfile.readline().decode("latin-1").strip()
            while self.rfile.readline().strip():
                pass
        except OSError:
            return
        if line:
            self.server.log(line)
        self.wfile.write(
            b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(len(_REFUSAL)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + _REFUSAL
        )


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Refuse)
        self.lines: list[str] = []
        self._lock = threading.Lock()

    def log(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)


class Proxy:
    def __init__(self) -> None:
        self._server = _Server()
        self._offset = 0
        threading.Thread(target=self._server.serve_forever, name="specrig-proxy", daemon=True).start()
        host, port = self._server.server_address[:2]
        self.address = f"http://{host}:{port}"

    def environment(self) -> dict[str, str | None]:
        return {**dict.fromkeys(_VARIABLES, self.address), "no_proxy": None, "NO_PROXY": None}

    def mark(self) -> None:
        self._offset = len(self._server.lines)

    def refused(self) -> list[str]:
        return [_named(line) for line in self._server.lines[self._offset :]]

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# A CONNECT names only the origin: the rest went inside the tunnel the proxy refused.
def _named(line: str) -> str:
    method, _, rest = line.partition(" ")
    target = rest.split(" ")[0]
    if method == "CONNECT":
        return "https://" + target.removesuffix(":443")
    return f"{method} {target}"
