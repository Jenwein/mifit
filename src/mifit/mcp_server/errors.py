"""Failures the MCP server reports to the AI client."""

from __future__ import annotations


class ToolFailure(Exception):
    """A failure whose message is safe to show the AI client.

    The message names the problem and the next step, and never holds a credential:
    no session field, no signed URL, no exception text from a network library.
    """
