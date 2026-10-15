"""Failures the MCP server reports to the AI client, and their messages.

Every message says what went wrong and what to do next, and holds no credential:
the SDK's own errors are written without secrets, and a network failure is named by
its exception type only, because the text of a ``requests`` error holds the URL it
was fetching and a URL can carry a signature (the CLI does the same).
"""

from __future__ import annotations

import requests

from mifit.errors import AuthError, MiFitError, RenewalError

__all__ = [
    "GPS_DISABLED",
    "LOGIN_ADVICE",
    "NOT_LOGGED_IN",
    "ToolFailure",
    "failure_for",
]

LOGIN_ADVICE = "Run `mifit login` in a terminal, then try again."
NOT_LOGGED_IN = "Not logged in to Mi Fitness. " + LOGIN_ADVICE
GPS_DISABLED = "GPS is disabled on this server; the user can start it with --allow-gps."


class ToolFailure(Exception):
    """A failure whose message is safe to show the AI client.

    The message names the problem and the next step, and never holds a credential:
    no session field, no signed URL, no exception text from a network library.
    """


def failure_for(exc: BaseException) -> ToolFailure | None:
    """The ``ToolFailure`` a read error becomes, or ``None`` for an error that is not
    a known read error (the caller re-raises it)."""
    if isinstance(exc, RenewalError):
        return ToolFailure(
            f"The Mi Fitness session expired and could not be renewed. {exc} {LOGIN_ADVICE}"
        )
    if isinstance(exc, AuthError):
        return ToolFailure(f"Mi Fitness refused the session. {exc} {LOGIN_ADVICE}")
    if isinstance(exc, MiFitError):
        return ToolFailure(str(exc))
    if isinstance(exc, requests.RequestException):
        return ToolFailure(f"Network request failed ({type(exc).__name__}).")
    return None
