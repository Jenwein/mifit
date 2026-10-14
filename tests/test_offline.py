"""The suite is offline: any attempt to reach the network fails the test."""

import socket

import pytest
import requests


def test_tests_cannot_reach_the_network():
    # Numeric address (TEST-NET-1): resolving it needs no DNS, so even the
    # unguarded red run sends nothing.
    with pytest.raises(RuntimeError, match="network"):
        socket.getaddrinfo("192.0.2.1", 443)
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    with socket.socket() as sock:
        sock.settimeout(1)
        with pytest.raises(RuntimeError, match="network"):
            sock.connect(("192.0.2.1", 443))
    with pytest.raises(RuntimeError, match="network"):
        requests.get("https://192.0.2.1/", timeout=1)
