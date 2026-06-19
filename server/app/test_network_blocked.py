import socket

import pytest
from pytest_socket import SocketBlockedError


def test_network_is_blocked_by_default() -> None:
    """Sockets are disabled in the test suite (`--disable-socket` in pyproject).

    This guards the invariant that tests never hit the network: external I/O
    (OpenAI, Weather, ICS feeds) must be faked, and HTTP is exercised through the
    Flask test client (in-process WSGI, no socket). A browser/integration test
    that genuinely needs sockets must opt in with @pytest.mark.enable_socket.
    """
    # pytest-socket warns and then raises when a test touches a socket.
    with pytest.warns(UserWarning), pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
