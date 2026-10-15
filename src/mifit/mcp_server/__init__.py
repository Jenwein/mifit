"""The MCP server: ``mifit mcp``.

A read-only Model Context Protocol server on stdio that AI clients (Claude Desktop,
Claude Code, Cursor, VS Code, ...) start on the user's own computer. It gives them
the B-layer records of each checked data domain and the profile, never ``raw``, with
local times and dense data cut down to at most 500 rows.

This package is the only part of mifit that imports the ``mcp`` SDK, and only when
the server runs, so ``import mifit`` and the other commands never pay for it.
"""
