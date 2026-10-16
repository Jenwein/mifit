"""Start an MCP server on stdio, talk to it like a client and list its tools.

Standard library only. The release workflow runs it on the Claude Desktop
bundle (``uv run ... src/server.py``); the ``smoke`` job keeps its own inline
copy because it runs without a checkout. Usage::

    python tools/mcp_handshake.py COMMAND [ARG...]

The arguments are the server's command, taken verbatim. On success it prints
``MCP server answered: <n> tools`` and exits 0; when the server fails, or does
not list ``get_status``, it prints ``error: <message>`` to stderr and exits 1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

MESSAGES = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp_handshake", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


def handshake(command: list[str], timeout_s: float = 120) -> set[str]:
    """Start ``command``, initialize it and return the names of the tools it lists.

    Like a client, it waits for each reply before sending the next message, because
    the server stops at the end of stdin and drops requests still in flight. Any
    failure raises ``RuntimeError`` with the server's stderr.
    """
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    watchdog = threading.Timer(timeout_s, proc.kill)
    watchdog.daemon = True  # never keeps the process alive after the check
    watchdog.start()
    replies = []
    try:
        try:
            for message in MESSAGES:
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if "id" in message:
                    line = proc.stdout.readline()
                    if not line:
                        raise RuntimeError(f"no reply to {message['method']}")
                    replies.append(json.loads(line))
        except (OSError, ValueError, RuntimeError) as exc:
            proc.kill()
            _, err = proc.communicate()
            raise RuntimeError(
                f"the server did not answer ({exc}); stderr: {err.strip()}"
            ) from exc
        # communicate() closes stdin itself (closing it first fails on Python 3.12).
        rest, err = proc.communicate()
    finally:
        watchdog.cancel()
    if proc.returncode != 0:
        raise RuntimeError(f"the server exited with {proc.returncode}; stderr: {err.strip()}")
    if rest:
        raise RuntimeError(f"the server wrote more than its replies to stdout: {rest[:200]!r}")
    if [reply.get("id") for reply in replies] != [1, 2] or "result" not in replies[1]:
        raise RuntimeError(f"unexpected replies: {replies!r}")
    return {tool["name"] for tool in replies[1]["result"]["tools"]}


def main(argv: list[str] | None = None) -> int:
    command = sys.argv[1:] if argv is None else argv
    try:
        names = handshake(command)
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if "get_status" not in names:
        print(f"error: the server lists no get_status tool: {sorted(names)}", file=sys.stderr)
        return 1
    print(f"MCP server answered: {len(names)} tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
