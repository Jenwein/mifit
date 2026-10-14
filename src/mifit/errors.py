"""Exception types raised by the mifit SDK."""

from __future__ import annotations

from typing import Any


class MiFitError(Exception):
    """Base class for all mifit errors."""


class AuthError(MiFitError):
    """The server rejected the request as unauthorized (HTTP 401).

    A 401 has more than one cause (a stale session, but also a request whose
    signature the server refused), so callers should not assume the session
    has expired.
    """


class ApiError(MiFitError):
    """A non-successful API outcome: HTTP error, undecryptable body, or ``code != 0``.

    ``code`` is the business ``code`` from the decrypted payload when there is one;
    ``payload`` carries whatever structured detail is available for diagnosis.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload
