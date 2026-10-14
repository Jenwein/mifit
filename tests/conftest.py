"""Make the ``src`` layout importable and keep the test suite off the network.

pyproject.toml sets ``pythonpath = ["src"]`` for pytest, and developers install
the package with ``pip install -e ".[test]"`` (see the README). Putting ``src``
on ``sys.path`` here as well keeps ``mifit`` importable when neither applies,
e.g. if pytest is pointed at another config file with ``-c``.

Every test also runs with the network refused and with the home directory moved
under pytest's ``tmp_path``, so no test can read or write the real ``~/.mifit``
(the session file and the session log). The network guard raises
``RuntimeError`` rather than an ``OSError``: an ``OSError`` would be turned into
an ordinary network error by urllib3/requests and by the CLI, which would hide
the guard behind a handled failure instead of failing the test.

Limits of the guard: it only covers code that runs in the test process while a
test function executes. It does not cover module import time, fixtures with a
broader scope than ``function``, or child processes. On Windows it also blocks
asyncio and ``socket.socketpair`` (whose fallback there uses ``connect``), so a
future async test would need a loopback allowance.
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
def _isolated_home(monkeypatch, tmp_path):
    """Point the home directory at a per-test temp dir.

    ``Path.home()`` reads ``USERPROFILE`` on Windows and ``HOME`` elsewhere; both
    are set, so ``paths.app_data_dir()`` resolves under ``tmp_path`` on every
    platform, for code in this process and for child processes that inherit the
    environment. The directory is not created: code that writes there makes it.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that tries to resolve a name or open a connection."""
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
