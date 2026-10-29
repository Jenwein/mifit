"""Failures the MCP server reports to the AI client, and their messages.

Every message says what went wrong and what to do next, and holds no credential:
the SDK's own errors are written without secrets, and a network failure is named by
its exception type only, because the text of a ``requests`` error holds the URL it
was fetching and a URL can carry a signature (the CLI does the same).

The login advice depends on where the server runs: ``LOGIN_ADVICE`` for the stdio
server on the user's computer, ``REMOTE_LOGIN_ADVICE`` for the HTTP server, whose
session lives on the server and not where the user sits.
"""

from __future__ import annotations

import requests

from mifit.errors import AuthError, MiFitError, RenewalError

__all__ = [
    "GPS_DISABLED",
    "LOGIN_ADVICE",
    "NOT_LOGGED_IN",
    "REMOTE_LOGIN_ADVICE",
    "ToolFailure",
    "failure_for",
    "not_logged_in",
]

LOGIN_ADVICE = "Run `mifit login` in a terminal or call login_start, then try again."
REMOTE_LOGIN_ADVICE = (
    "Call login_start, or run `mifit login` in a terminal on the server mifit runs on, "
    "then try again."
)


def not_logged_in(advice: str = LOGIN_ADVICE) -> str:
    """The message for a missing session, ending with ``advice``."""
    return "Not logged in to Mi Fitness. " + advice


NOT_LOGGED_IN = not_logged_in()
GPS_DISABLED = (
    "GPS is disabled on this server. The user can allow it with the Allow GPS tracks "
    "switch of the Claude Desktop extension, or by starting the server with --allow-gps."
)


class ToolFailure(Exception):
    """A failure whose message is safe to show the AI client.

    The message names the problem and the next step, and never holds a credential:
    no session field, no signed URL, no exception text from a network library.
    """


def failure_for(exc: BaseException, *, advice: str = LOGIN_ADVICE) -> ToolFailure | None:
    """The ``ToolFailure`` a read error becomes, or ``None`` for an error that is not
    a known read error (the caller re-raises it). A refused session ends with
    ``advice``."""
    if isinstance(exc, RenewalError):
        return ToolFailure(
            f"The Mi Fitness session expired and could not be renewed. {exc} {advice}"
        )
    if isinstance(exc, AuthError):
        return ToolFailure(f"Mi Fitness refused the session. {exc} {advice}")
    if isinstance(exc, MiFitError):
        return ToolFailure(str(exc))
    if isinstance(exc, requests.RequestException):
        return ToolFailure(f"Network request failed ({type(exc).__name__}).")
    return None
