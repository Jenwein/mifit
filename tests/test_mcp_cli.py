"""``mifit mcp``: the command, the stdio protocol stream and the lazy SDK import.

The subprocess tests start the real server on stdio. They need no network:
``initialize`` and ``tools/list`` read no data, and the child's home directory is the
test's own (conftest sets it in the environment the child inherits).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import mifit
from mcp_fakes import P1_TOOLS
from mifit import cli, mcp_server
from mifit.mcp_server.server import INSTRUCTIONS

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

HANDSHAKE = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


def _env():
    return {**os.environ, "PYTHONPATH": str(SRC)}


def test_mcp_command_passes_the_gps_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_server, "run", lambda *, allow_gps: seen.append(allow_gps))
    assert cli.main(["mcp"]) == 0
    assert cli.main(["mcp", "--allow-gps"]) == 0
    assert seen == [False, True]


def test_mcp_command_is_in_the_help(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "mcp" in out and "run the MCP server for AI assistants (stdio, read-only)" in out


def test_stdout_carries_only_json_rpc():
    proc = subprocess.run(
        [sys.executable, "-m", "mifit.cli", "mcp"],
        input="".join(json.dumps(m) + "\n" for m in HANDSHAKE),
        capture_output=True,
        text=True,
        env=_env(),
        cwd=ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert all(r["jsonrpc"] == "2.0" for r in replies)
    init = replies[0]["result"]
    assert (init["serverInfo"]["name"], init["serverInfo"]["version"]) == (
        "mifit",
        mifit.__version__,
    )
    assert init["instructions"] == INSTRUCTIONS
    assert {t["name"] for t in replies[1]["result"]["tools"]} == P1_TOOLS
    assert "MCP server on stdio (GPS tracks off)" in proc.stderr


def test_the_gps_flag_reaches_the_server_line():
    proc = subprocess.run(
        [sys.executable, "-m", "mifit.cli", "mcp", "--allow-gps"],
        input="",
        capture_output=True,
        text=True,
        env=_env(),
        cwd=ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert "MCP server on stdio (GPS tracks on)" in proc.stderr


def test_mifit_and_its_cli_do_not_import_the_sdk():
    code = "import sys, mifit, mifit.cli; sys.exit('mcp' in sys.modules)"
    proc = subprocess.run(
        [sys.executable, "-c", code], env=_env(), cwd=ROOT, timeout=60
    )
    assert proc.returncode == 0
