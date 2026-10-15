"""The MCP server's one ``MiFit`` client, read one call at a time.

The client is opened lazily, on the first read, from the stored session (the one
``mifit login`` saves), and kept: it renews itself and saves the renewal, as the CLI's
does. Reads hold one lock, so only one request is in flight at a time, whatever the
AI client sends at once; ``Transport``'s pacing does the rest.

When a read is refused (``AuthError``), the stored session is loaded again: the user
may have run ``mifit login`` in a terminal meanwhile, or another process renewed it.
Only a session that differs from the one in use is tried; the same session would
only be refused again, after one more renewal attempt.

Read errors become ``ToolFailure``s with safe messages (``errors.failure_for``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from mifit.errors import AuthError
from mifit.mcp_server.errors import NOT_LOGGED_IN, ToolFailure, failure_for

__all__ = ["Access"]

T = TypeVar("T")


class Access:
    """The server's client. ``factory`` opens one from the stored session (``MiFit``)
    and raises ``AuthError`` when there is none it can use."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._client: Any = None
        self._lock = threading.Lock()

    def read(self, fn: Callable[[Any], T]) -> T:
        """``fn(client)``, with the client opened if needed, under the read lock.

        Raises ``ToolFailure`` for a missing session and for the read errors
        ``failure_for`` knows; anything else propagates unchanged.
        """
        with self._lock:
            client = self._client if self._client is not None else self._open()
            failure = None
            try:
                return fn(client)
            except AuthError as exc:
                failure = self._after_refusal(client, exc)
            except Exception as exc:
                failure = failure_for(exc)
                if failure is None:
                    raise
            # Raised (or retried) here, outside the ``except`` blocks, so that a
            # failure leads back to no original: its text may hold a signed URL.
            if failure is None:  # refused, and a different stored session is open
                return self._retry(fn)
            raise failure

    def reset(self) -> None:
        """Drop the client; the next read opens the stored session again."""
        with self._lock:
            self._client = None

    def _open(self) -> Any:
        """A client for the stored session, kept for later reads."""
        try:
            self._client = self._factory()
        except AuthError:
            self._client = None
            missing = True
        else:
            missing = False
        if missing:
            raise ToolFailure(NOT_LOGGED_IN)
        return self._client

    def _after_refusal(self, client: Any, refusal: AuthError) -> ToolFailure | None:
        """What a refused read becomes: ``None`` when a different stored session was
        opened to retry with, else the failure to report."""
        try:
            fresh = self._factory()
        except AuthError:
            self._client = None
            return ToolFailure(NOT_LOGGED_IN)
        if fresh.session.service_token == client.session.service_token:
            self._client = client
            return failure_for(refusal)
        self._client = fresh
        return None

    def _retry(self, fn: Callable[[Any], T]) -> T:
        """``fn`` on the newly opened client, its errors mapped as on the first try."""
        failure = None
        try:
            return fn(self._client)
        except Exception as exc:
            failure = failure_for(exc)
            if failure is None:
                raise
        raise failure
