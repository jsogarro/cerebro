"""The conftest guard blocks non-loopback sockets in unit tests."""

import contextlib
import socket

import pytest


def test_non_loopback_connect_is_blocked_and_recorded(
    block_external_sockets: list[str],
) -> None:
    with socket.socket() as sock, pytest.raises(OSError, match="non-loopback"):
        sock.connect(("203.0.113.1", 443))
    assert len(block_external_sockets) == 1
    block_external_sockets.clear()  # recorded as expected; don't fail teardown


def test_loopback_connect_is_not_blocked() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())


@pytest.mark.live_eval
def test_live_marker_is_exempt() -> None:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        # unreachable is fine; the guard must not have intercepted it
        with contextlib.suppress(OSError):
            sock.connect(("203.0.113.1", 443))
