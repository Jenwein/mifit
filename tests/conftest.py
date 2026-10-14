"""Make the ``src`` layout importable for tests without installing the package.

Packaging (pyproject.toml, console entry points) is added in a later task; until
then tests resolve ``mifit`` by putting ``src`` on ``sys.path``.

Every test also runs with the network refused, raising ``RuntimeError`` rather than an
``OSError`` (urllib3/requests and the CLI would turn that into an ordinary network error
and hide the guard).
"""

import socket
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _refuse(*args, **kwargs):
    raise RuntimeError("tests must not use the network")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that tries to resolve a name or open a connection."""
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
