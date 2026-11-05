import socket

import pytest

from specrig.gate import Gate


def test_a_refusing_gate_takes_no_connection_and_gets_its_port_back_when_reopened() -> None:
    gate = Gate(lambda: ("127.0.0.1", 9))

    gate.refuse()
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", gate.port), timeout=5)

    gate.reopen()
    socket.create_connection(("127.0.0.1", gate.port), timeout=5).close()
    gate.refuse()
