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


class RenewalError(AuthError):
    """The session expired or was refused and could not be renewed automatically; log in again.

    Raised when the stored passToken cannot be traded for a new service token:
    the session has no passToken, Xiaomi refused it, asked for a security
    confirmation, or the token exchange failed. ``code`` is the account
    service's business code when it refused the passToken (e.g. ``70016``),
    else ``None``. The message states the reason only, never a credential.
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


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


class NotificationRequiredError(MiFitError):
    """The login needs a security confirmation that only the account owner can give.

    Xiaomi answers a QR login with a non-zero ``securityStatus`` and a
    ``notificationUrl`` when it wants the user to approve the sign-in on a
    page or device of their own. Open ``notification_url`` yourself, finish the
    confirmation, then start the login again. The URL is kept out of the message
    so it does not end up in logs; read it from the attribute.
    """

    def __init__(self, notification_url: str) -> None:
        super().__init__(
            "Xiaomi requires a security confirmation before this login can finish; "
            "open the URL in the exception's notification_url attribute, complete "
            "the confirmation, then log in again."
        )
        self.notification_url = notification_url
