"""The MCP server: ``mifit mcp``.

A read-only Model Context Protocol server on stdio that AI clients (Claude Desktop,
Claude Code, Cursor, VS Code, ...) start on the user's own computer. It gives them
the B-layer records of each checked data domain and the profile, never ``raw``, with
local times and dense data cut down to at most 500 rows.

This package is the only part of mifit that imports the ``mcp`` SDK, and only when
the server runs, so ``import mifit`` and the other commands never pay for it.
"""

from __future__ import annotations

import sys

import mifit

__all__ = ["run"]


def run(*, allow_gps: bool = False) -> None:
    """Serve MCP on stdin/stdout until the client closes stdin (``mifit mcp``).

    ``allow_gps`` opens the server's GPS gate: only then can ``get_workout_detail``
    return a track, and only when asked. One line about the server goes to stderr;
    stdout carries the protocol alone.
    """
    from mifit.mcp_server.server import build_server  # the SDK is loaded only here

    gps = "on" if allow_gps else "off"
    print(
        f"mifit {mifit.__version__}: MCP server on stdio (GPS tracks {gps})",
        file=sys.stderr,
    )
    build_server(allow_gps=allow_gps).run()
