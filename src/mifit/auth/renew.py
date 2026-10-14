"""Renew a session's service token from its stored passToken, without a QR scan.

This repeats what the Mi Fitness app's account SDK does when its own service
token is refused: ``XMPassport.loginByPassToken`` sends
``GET https://account.xiaomi.com/pass/serviceLogin?sid=<sid>&_json=true`` with
the ``userId``, ``passToken`` and ``deviceId`` cookies, parses the answer with
the same ``processLoginContent`` rules as the QR long poll, and trades the
result for a new service token at the STS URL it names. Here that is
``qr._parse_login_content`` followed by ``qr.exchange_sts``. The app's
``_locale`` parameter and ``uDevId`` cookie are not sent. See
``notes/09-session-lifetime-renewal.md`` for the APK evidence and the unknowns.

Redirects are not followed, because ``requests`` would carry the passToken
cookie to the redirect target; a 3xx answer is a failure. Every failure the
account service reports becomes a ``RenewalError`` (an ``AuthError``) whose
message states the reason only, never a credential. Transport-level exceptions
raised by ``http`` itself (timeouts, connection errors) propagate unchanged.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from mifit.auth.qr import (
    _REQUEST_TIMEOUT_S,
    _get,
    _http_or_default,
    _parse_login_content,
    _utc_now_iso,
    exchange_sts,
)
from mifit.auth.session import Session
from mifit.errors import ApiError, NotificationRequiredError, RenewalError
from mifit.region import SID

__all__ = ["RENEW_URL", "renew_session"]

RENEW_URL = "https://account.xiaomi.com/pass/serviceLogin"


def renew_session(session: Session, http: Any = None, *, sid: str = SID) -> Session:
    """Trade ``session``'s passToken for a new service token and return the renewed session.

    The renewed session has the new service token, ``ssecurity`` and STS cookies,
    and keeps ``region``, ``created_at`` (the login time) and ``device_id``. It
    takes the passToken the server returned (rotation) or keeps the old one, and
    sets ``renewed_at`` to the current UTC time. ``session`` itself is not changed.

    Raises ``RenewalError`` when ``session.can_renew()`` is false (no passToken or
    user id; before any request) or the renewal fails: an HTTP status other than
    2xx (3xx included), a refused passToken (``.code`` keeps the account service's
    code), a demanded security confirmation, an incomplete answer or a failed STS
    exchange.
    """
    if not session.can_renew():
        raise RenewalError(
            "This session has no passToken or user id to renew with (a session saved "
            "before automatic renewal existed has no passToken)."
        )

    http = _http_or_default(http)
    cookies = {"userId": session.user_id, "passToken": session.pass_token}
    if session.device_id:
        cookies["deviceId"] = session.device_id
    response = _get(
        http,
        RENEW_URL,
        params={"sid": sid, "_json": "true"},
        cookies=cookies,
        timeout=_REQUEST_TIMEOUT_S,
        allow_redirects=False,
    )

    known = {
        "userId": session.user_id,
        "passToken": session.pass_token,
        "cUserId": session.c_user_id,
    }
    # Each `from None` drops the original exception, whose payload and context
    # were never meant for a caller's logs.
    try:
        result = _parse_login_content(response, "serviceLogin", known=known)
        renewed = exchange_sts(result, sid, http, device_id=session.device_id)
    except NotificationRequiredError:
        raise RenewalError(
            "Xiaomi asked for a security confirmation before it would renew the session."
        ) from None
    except ApiError as exc:
        if exc.code is not None:
            raise RenewalError(
                f"Xiaomi refused the stored passToken (code {exc.code}).", code=exc.code
            ) from None
        raise RenewalError(exc.message) from None

    return dataclasses.replace(
        renewed,
        region=session.region,
        created_at=session.created_at,
        pass_token=result.pass_token,
        renewed_at=_utc_now_iso(),
    )
