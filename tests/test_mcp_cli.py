"""``mifit mcp``: the command, the stdio protocol stream, the HTTP server and the
lazy SDK import.

The subprocess tests start the real server, on stdio or on HTTP over loopback. They
need no network beyond loopback: ``initialize``, ``tools/list`` and ``get_status``
read no data, and the child's home directory is the test's own (conftest sets it in
the environment the child inherits). The HTTP client is a child process too
(``mcp_http_probe.py``), as the network guard covers this process only.
"""

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from mcp.server import MCPServer

import mifit
from mcp_fakes import P2_TOOLS, list_tools
from mifit import cli, mcp_server
from mifit.mcp_server import errors, tools
from mifit.mcp_server.server import INSTRUCTIONS, REMOTE_PRIVACY_NOTE

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROBE = Path(__file__).resolve().parent / "mcp_http_probe.py"

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
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
]


def _env():
    return {**os.environ, "PYTHONPATH": str(SRC)}


def _serve(*args, messages):
    """Talk to ``mifit mcp`` as a client does: send each message, wait for the reply
    to each request, and close stdin only after the last reply. (The server stops at
    the end of stdin and drops requests still in flight, so writing everything and
    closing at once loses replies at random.)

    Returns the exit code, the replies in order, whatever stdout held after them,
    and stderr.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "mifit.cli", "mcp", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_env(),
        cwd=ROOT,
    )
    watchdog = threading.Timer(120, proc.kill)
    watchdog.start()
    try:
        replies = []
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
            if "id" in message:
                replies.append(json.loads(proc.stdout.readline()))
        # communicate() closes stdin itself (closing it first fails on Python 3.12,
        # whose communicate() flushes stdin without checking that it is open).
        rest, err = proc.communicate()
    finally:
        watchdog.cancel()
    return proc.returncode, replies, rest, err


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
    code, replies, rest, err = _serve(messages=HANDSHAKE)
    assert code == 0, err
    assert rest == ""  # nothing on stdout but the replies
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert all(r["jsonrpc"] == "2.0" for r in replies)
    status = json.loads(replies[2]["result"]["content"][0]["text"])
    assert status["tool"] == "get_status" and status["logged_in"] is False
    init = replies[0]["result"]
    assert (init["serverInfo"]["name"], init["serverInfo"]["version"]) == (
        "mifit",
        mifit.__version__,
    )
    assert init["instructions"] == INSTRUCTIONS
    assert {t["name"] for t in replies[1]["result"]["tools"]} == P2_TOOLS
    # The protocol stream is UTF-8 whatever the console code page.
    descriptions = {t["name"]: t["description"] for t in replies[1]["result"]["tools"]}
    assert "活力指标" in descriptions["get_activity"]
    assert "MCP server on stdio (GPS tracks off)" in err


def test_the_gps_flag_reaches_the_server_line():
    proc = subprocess.run(
        [sys.executable, "-m", "mifit.cli", "mcp", "--allow-gps"],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
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


# --- the HTTP transport --------------------------------------------------------


@pytest.fixture
def http_runs(monkeypatch):
    """What ``mifit mcp --transport http`` passes to ``run_http``; ``run`` (stdio)
    must not be called."""
    runs = []
    monkeypatch.setattr(mcp_server, "run_http", lambda **kwargs: runs.append(kwargs))
    monkeypatch.setattr(mcp_server, "run", lambda **kwargs: pytest.fail("stdio server started"))
    return runs


def test_http_transport_defaults(http_runs):
    assert cli.main(["mcp", "--transport", "http"]) == 0
    assert http_runs == [
        {
            "allow_gps": False,
            "host": "127.0.0.1",
            "port": 8766,
            "path": "/mcp",
            "allowed_hosts": (),
        }
    ]


def test_http_transport_options(http_runs):
    argv = [
        "mcp", "--transport", "http", "--allow-gps", "--host", "100.64.0.5",
        "--port", "9000", "--path", "/mifit/mcp",
        "--allowed-host", "mifit.ts.internal:9000", "--allowed-host", "mifit:*",
    ]
    assert cli.main(argv) == 0
    assert http_runs == [
        {
            "allow_gps": True,
            "host": "100.64.0.5",
            "port": 9000,
            "path": "/mifit/mcp",
            "allowed_hosts": ("mifit.ts.internal:9000", "mifit:*"),
        }
    ]


def test_a_bracketed_ipv6_host_is_listened_on_bare(http_runs):
    assert cli.main(["mcp", "--transport", "http", "--host", "[::1]"]) == 0
    assert http_runs[0]["host"] == "::1"


def test_stdio_is_still_the_default(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_server, "run", lambda **kwargs: seen.append(kwargs))
    monkeypatch.setattr(mcp_server, "run_http", lambda **kwargs: pytest.fail("HTTP server"))
    assert cli.main(["mcp", "--transport", "stdio"]) == 0
    assert seen == [{"allow_gps": False}]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--host", "100.64.0.5"], "mcp: --host applies only with --transport http"),
        (
            ["--transport", "stdio", "--port", "9000", "--allowed-host", "a:1"],
            "mcp: --port, --allowed-host apply only with --transport http",
        ),
        (["--path", "/mcp"], "mcp: --path applies only with --transport http"),
        (["--transport", "http", "--port", "0"], "mcp: --port must be between 1 and 65535"),
        (["--transport", "http", "--port", "65536"], "mcp: --port must be between 1 and 65535"),
        (["--transport", "http", "--path", "mcp"], "mcp: --path must start with / (such as /mcp)"),
        (["--transport", "http", "--host", " "], "mcp: --host must not be empty"),
        (["--transport", "http", "--allowed-host", "http://mifit:8766"], "--allowed-host takes"),
        (["--transport", "http", "--allowed-host", "mifit/mcp"], "--allowed-host takes"),
        (["--transport", "http", "--allowed-host", ""], "--allowed-host takes"),
        (["--transport", "sse"], "invalid choice: 'sse'"),
    ],
)
def test_mcp_usage_errors(http_runs, capsys, argv, message):
    assert cli.main(["mcp", *argv]) == 2
    assert message in capsys.readouterr().err
    assert http_runs == []


@pytest.mark.parametrize(
    "value", ["mifit.ts.internal:8766", "mifit.ts.internal", "mifit:*", "100.64.0.5:8766", "[fd7a::1]:8766"]
)
def test_allowed_host_values(http_runs, value):
    assert cli.main(["mcp", "--transport", "http", "--allowed-host", value]) == 0
    assert http_runs[0]["allowed_hosts"] == (value,)


def test_mcp_help_names_the_http_options(capsys):
    assert cli.main(["mcp", "--help"]) == 0
    out = capsys.readouterr().out
    for text in ("--transport {stdio,http}", "--host", "--port", "--path", "--allowed-host"):
        assert text in out, text
    assert "no authentication" in " ".join(out.split())  # argparse wraps the help


@pytest.mark.parametrize(
    ("host", "extra", "accepted"),
    [
        ("127.0.0.1", (), ["127.0.0.1:*", "localhost:*", "[::1]:*"]),
        ("localhost", (), ["127.0.0.1:*", "localhost:*", "[::1]:*"]),
        ("::1", (), ["127.0.0.1:*", "localhost:*", "[::1]:*"]),
        ("0.0.0.0", ("mifit.ts.internal:8766",), [
            "127.0.0.1:*", "localhost:*", "[::1]:*", "mifit.ts.internal:8766",
        ]),
        ("100.64.0.5", (), ["100.64.0.5:8766"]),
        ("100.64.0.5", ("mifit.ts.internal:8766", "100.64.0.5:8766"), [
            "100.64.0.5:8766", "mifit.ts.internal:8766",
        ]),
        ("fd7a:115c:a1e0::5", (), ["[fd7a:115c:a1e0::5]:8766"]),
        ("mifit.ts.internal", (), ["mifit.ts.internal:8766"]),
        # Another loopback address is accepted as dialled; names are lowered.
        ("127.0.0.2", (), ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.2:*"]),
        ("LOCALHOST", (), ["127.0.0.1:*", "localhost:*", "[::1]:*"]),
        ("100.64.0.5", ("Mifit.TS.internal:8766",), ["100.64.0.5:8766", "mifit.ts.internal:8766"]),
    ],
)
def test_accepted_hosts(host, extra, accepted):
    assert mcp_server.accepted_hosts(host, 8766, extra) == accepted


@pytest.fixture
def sdk_runs(monkeypatch):
    """The servers ``run_http`` starts and how: ``(server, transport, kwargs)``."""
    runs = []

    def run(self, transport="stdio", **kwargs):
        runs.append((self, transport, kwargs))

    monkeypatch.setattr(MCPServer, "run", run)
    return runs


@pytest.mark.usefixtures("allow_loopback")
def test_run_http_starts_the_sdk_server_with_the_host_check(sdk_runs, capsys):
    mcp_server.run_http(host="100.64.0.5", port=8766, allowed_hosts=["mifit.ts.internal:8766"])
    [(server, transport, kwargs)] = sdk_runs
    assert transport == "streamable-http"
    security = kwargs.pop("transport_security")
    assert kwargs == {"host": "100.64.0.5", "port": 8766, "streamable_http_path": "/mcp"}
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["100.64.0.5:8766", "mifit.ts.internal:8766"]
    assert security.allowed_origins == [
        "http://100.64.0.5:8766", "https://100.64.0.5:8766",
        "http://mifit.ts.internal:8766", "https://mifit.ts.internal:8766",
    ]
    # The server is the remote one: its login opens nothing on this machine.
    listed = {tool.name: tool.description for tool in list_tools(server)}
    assert listed["login_start"] == tools.REMOTE_LOGIN_START_DESCRIPTION
    err = capsys.readouterr().err
    assert (
        f"mifit {mifit.__version__}: MCP server on http://100.64.0.5:8766/mcp (GPS tracks off)"
        in err
    )
    assert "accepted Host headers: 100.64.0.5:8766, mifit.ts.internal:8766" in err
    assert "warning: this server has no authentication" in " ".join(err.split())


def test_run_http_on_loopback_keeps_the_host_check_and_does_not_warn(sdk_runs, capsys):
    mcp_server.run_http(allow_gps=True, host="::1", port=9000, path="/x")
    [(_, _, kwargs)] = sdk_runs
    security = kwargs["transport_security"]
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == list(mcp_server.LOOPBACK_HOSTS)
    err = capsys.readouterr().err
    assert "MCP server on http://[::1]:9000/x (GPS tracks on)" in err
    assert "warning" not in err


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("allowed", [True, False], ids=["allowed-host", "no-allowed-host"])
def test_http_server_answers_a_client(tmp_path, allowed):
    """The real ``mifit mcp --transport http`` on loopback, and the SDK's client."""
    port = _free_port()
    name = f"mifit.test:{port}"
    extra = ["--allowed-host", name] if allowed else []
    # A proxy in the environment would take the client's requests elsewhere.
    env = {k: v for k, v in _env().items() if not k.lower().endswith("_proxy")}
    log = tmp_path / "server-stderr.txt"
    with log.open("w", encoding="utf-8") as stderr:
        server = subprocess.Popen(
            [sys.executable, "-m", "mifit.cli", "mcp", "--transport", "http",
             "--port", str(port), *extra],
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            env=env,
            cwd=ROOT,
        )
        try:
            probe = subprocess.run(
                [sys.executable, str(PROBE), f"http://127.0.0.1:{port}/mcp",
                 f"127.0.0.1:{port}", f"localhost:{port}", name, f"other.test:{port}",
                 f"127.0.0.1:{port},http://127.0.0.1:{port}",
                 f"127.0.0.1:{port},http://evil.test"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=ROOT,
                timeout=120,
            )
        finally:
            server.terminate()
            server.wait(timeout=30)
    err = log.read_text(encoding="utf-8")
    assert probe.returncode == 0, probe.stderr + err
    result = json.loads(probe.stdout)
    assert result["server"] == {"name": "mifit", "version": mifit.__version__}
    assert set(result["tools"]) == P2_TOOLS
    assert result["tools"]["login_start"] == tools.REMOTE_LOGIN_START_DESCRIPTION
    status = result["status"]
    assert status["logged_in"] is False and status["privacy"] == REMOTE_PRIVACY_NOTE
    assert status["next_step"] == errors.not_logged_in(errors.REMOTE_LOGIN_ADVICE)
    assert result["hosts"] == {
        f"127.0.0.1:{port}": 200,
        f"localhost:{port}": 200,
        name: 200 if allowed else 421,  # 421: the SDK's DNS rebinding protection
        f"other.test:{port}": 421,
        f"127.0.0.1:{port},http://127.0.0.1:{port}": 200,
        f"127.0.0.1:{port},http://evil.test": 403,  # a page of another origin
    }
    assert f"MCP server on http://127.0.0.1:{port}/mcp (GPS tracks off)" in err
    assert "warning" not in err
