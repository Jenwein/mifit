# Portions of this file are adapted from Mi-Fitness-Sync
# (https://github.com/kevinkwee/Mi-Fitness-Sync),
# src/mi_fitness_sync/auth/client.py,
# used under the MIT License. See THIRD_PARTY_NOTICES for the
# upstream copyright notice and license text.
#
# Upstream: Copyright (c) 2026 Michael Elian Kevin

"""QR-code login (longPolling) and the STS exchange for a Mi service token.

The flow mirrors the Xiaomi account SDK that ships inside the Mi Fitness APK:

1. ``request_qr_login`` asks ``account.xiaomi.com`` for a QR login session and
   gets back the page/QR to show the user plus a long-polling URL.
2. The user approves the login themselves (scanning the code in an official
   Xiaomi app or page); ``poll_qr_login`` waits on the long-polling URL and
   parses the login result.
3. ``exchange_sts`` signs the login nonce with the account ``ssecurity`` and
   calls the STS URL from the result, which answers with the service-scoped
   token as a cookie.

``login_qr`` strings the three together. Every function takes an injectable
``http`` (anything with ``.get(url, ...)`` returning an object with
``.status_code``, ``.text``, ``.cookies`` and ``.headers``; a ``requests.Session``
by default) so tests run without a network.

Secrets (``passToken``, ``ssecurity``, the STS URL and the service token) are
never logged, never put in exception messages and are hidden from ``repr``.
Transport-level exceptions raised by ``http`` itself (timeouts, connection
errors) propagate unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

from mifit.auth.session import Session
from mifit.errors import ApiError, NotificationRequiredError
from mifit.region import COUNTRY_CODE_TO_REGION, SID, region_for_country_code

__all__ = [
    "ACCOUNT_DOMAIN",
    "LoginResult",
    "QrChallenge",
    "exchange_sts",
    "login_qr",
    "new_device_id",
    "poll_qr_login",
    "request_qr_login",
]

ACCOUNT_DOMAIN = "https://account.xiaomi.com"
QR_LOGIN_URL_ENDPOINT = ACCOUNT_DOMAIN + "/longPolling/loginUrl"
SAFE_PREFIX = "&&&START&&&"

_REQUEST_TIMEOUT_S = 30.0
# The long poll is held open by the server until the user approves (or it gives up),
# so its read timeout must be far longer than an ordinary request's.
_LONG_POLL_TIMEOUT_S = 120.0

# Keys under which a login result might carry the account's country/region. None of
# them is confirmed against a live response; the hint is optional and best-effort.
_REGION_HINT_KEYS = ("userRegion", "user_region", "region", "countryCode", "country")
_KNOWN_REGIONS = frozenset(COUNTRY_CODE_TO_REGION.values()) | {"cn"}
_REDACTED = "<redacted>"


@dataclass(frozen=True)
class QrChallenge:
    """What the server returned for a QR login session.

    ``login_url`` and ``qr_payload`` are the server's ``loginUrl`` and ``qr``
    fields, passed through unchanged for a UI to show. ``lp_url`` is the
    long-polling URL for ``poll_qr_login``; it is session-bound, so it is kept
    out of ``repr``.
    """

    login_url: str
    qr_payload: str
    lp_url: str = field(repr=False)


@dataclass(repr=False)
class LoginResult:
    """The parsed outcome of an approved QR login (before the STS exchange).

    ``nonce`` is the account login nonce as decimal text (or an int, when built
    by hand); it is not the per-request nonce used to sign health requests.
    ``region_hint`` is an optional country/region the login response carried.
    """

    pass_token: str
    c_user_id: str
    ssecurity: str
    nonce: int | str
    location: str
    user_id: str
    region_hint: str | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"user_id={self.user_id!r}, "
            f"c_user_id={self.c_user_id!r}, "
            f"pass_token={_REDACTED}, "
            f"ssecurity={_REDACTED}, "
            f"nonce={_REDACTED}, "
            f"location={_REDACTED}, "
            f"region_hint={self.region_hint!r})"
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _http_or_default(http: Any) -> Any:
    if http is not None:
        return http
    import requests  # imported lazily so an injected ``http`` needs no requests

    return requests.Session()


def _get(
    http: Any,
    url: str,
    *,
    timeout: float,
    params: dict[str, str] | None = None,
    allow_redirects: bool | None = None,
    cookies: dict[str, str] | None = None,
) -> Any:
    kwargs: dict[str, Any] = {"timeout": timeout}
    if params is not None:
        kwargs["params"] = params
    if allow_redirects is not None:
        kwargs["allow_redirects"] = allow_redirects
    if cookies is not None:
        kwargs["cookies"] = cookies
    return http.get(url, **kwargs)


def _device_cookies(device_id: str | None) -> dict[str, str] | None:
    """The ``deviceId`` cookie the account service gets, or ``None`` for no cookies."""
    return {"deviceId": device_id} if device_id else None


def _text(value: Any) -> str:
    """A JSON scalar as stripped text; anything else (or null) is ``""``."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


def _nonce_text(value: Any) -> str | None:
    """The login nonce as exact decimal text, or ``None`` if absent/unusable."""
    number = _to_int(value)
    return None if number is None else str(number)


def _load_json(response: Any, what: str) -> dict[str, Any]:
    """Check the HTTP status, strip the safe prefix and parse a JSON object."""
    status = response.status_code
    if not 200 <= status < 300:
        raise ApiError(f"{what} failed with HTTP {status}.", payload={"http_status": status})
    body = response.text.strip()
    if body.startswith(SAFE_PREFIX):
        body = body[len(SAFE_PREFIX) :]
    try:
        payload = json.loads(body)
    except ValueError:
        # `from None`: the decode error carries a fragment of the body.
        raise ApiError(f"{what} returned a body that is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise ApiError(f"{what} returned a JSON value that is not an object.")
    return payload


def _raise_for_code(payload: dict[str, Any], what: str) -> None:
    code = _to_int(payload.get("code"))
    if code is None:
        raise ApiError(f"{what} response has no numeric code.", payload={"fields": sorted(payload)})
    if code != 0:
        description = _text(payload.get("desc")) or _text(payload.get("description"))
        raise ApiError(
            f"{what} was refused (code {code})" + (f": {description}" if description else "."),
            code=code,
            payload={"code": code, "description": description},
        )


def _missing_fields_error(what: str, missing: list[str], payload: dict[str, Any]) -> ApiError:
    # Only field *names* go into the error: the values can be credentials.
    return ApiError(
        f"{what} did not include: {', '.join(missing)}.",
        payload={"fields": sorted(payload), "missing": missing},
    )


def _extension_pragma(response: Any) -> dict[str, Any]:
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Extension-Pragma") or headers.get("extension-pragma")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _client_sign(nonce: str, ssecurity: str) -> str:
    """STS client signature: ``base64(SHA1("nonce=<nonce>&<ssecurity>"))``.

    ``ssecurity`` goes in as the raw string the login returned. This is a plain
    SHA-1, distinct from the RC4 key derived from ``ssecurity`` that signs the
    health-API requests.
    """
    digest = hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _set_cookie_values(response: Any) -> list[str]:
    raw = getattr(response, "raw", None)
    getlist = getattr(getattr(raw, "headers", None), "getlist", None)
    if callable(getlist):
        values = list(getlist("Set-Cookie"))
        if values:
            return values
    headers = getattr(response, "headers", None) or {}
    single = headers.get("Set-Cookie") or headers.get("set-cookie")
    return [single] if single else []


def _response_cookies(response: Any) -> dict[str, str]:
    """Cookies set by ``response``: the cookie jar first, raw ``Set-Cookie`` as backup.

    The APK reads the token straight from the response headers, whereas a client
    cookie jar can drop a cookie whose domain does not match the request host, so
    the raw headers are consulted for anything the jar lacks.
    """
    cookies: dict[str, str] = {}
    jar = getattr(response, "cookies", None)
    if jar is not None:
        for name, value in jar.items():
            cookies[str(name)] = str(value)
    for header in _set_cookie_values(response):
        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(header)
        except CookieError:
            continue
        for name, morsel in parsed.items():
            cookies.setdefault(name, morsel.value)
    return cookies


def _resolve_region(hint: str | None) -> str:
    """Map a country code or backend region code to a region; default ``cn``."""
    text = (hint or "").strip()
    if not text:
        return "cn"
    region = region_for_country_code(text)
    if region:
        return region
    if text.lower() in _KNOWN_REGIONS:
        return text.lower()
    return "cn"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def new_device_id() -> str:
    """A random device id: 16 uppercase hex characters, as the reference client makes."""
    return secrets.token_hex(8).upper()


def request_qr_login(
    http: Any = None, sid: str = SID, *, device_id: str | None = None
) -> QrChallenge:
    """Ask the account service for a QR login session for ``sid``.

    GETs ``https://account.xiaomi.com/longPolling/loginUrl?sid=<sid>&_json=true``
    and returns the ``loginUrl``, ``qr`` and ``lp`` fields. With a ``device_id`` the
    request carries it as the ``deviceId`` cookie. Raises ``ApiError`` on an HTTP
    error, a non-zero ``code``, an unparsable body or a missing field.
    """
    http = _http_or_default(http)
    url = f"{QR_LOGIN_URL_ENDPOINT}?{urlencode([('sid', sid), ('_json', 'true')])}"
    what = "QR login request"
    response = _get(
        http, url, timeout=_REQUEST_TIMEOUT_S, cookies=_device_cookies(device_id)
    )
    payload = _load_json(response, what)
    _raise_for_code(payload, what)

    login_url = _text(payload.get("loginUrl"))
    qr_payload = _text(payload.get("qr"))
    lp_url = _text(payload.get("lp"))
    missing = [
        name
        for name, value in (("loginUrl", login_url), ("qr", qr_payload), ("lp", lp_url))
        if not value
    ]
    if missing:
        raise _missing_fields_error("QR login response", missing, payload)
    return QrChallenge(login_url=login_url, qr_payload=qr_payload, lp_url=lp_url)


def poll_qr_login(
    challenge: QrChallenge,
    http: Any = None,
    *,
    timeout_s: float = _LONG_POLL_TIMEOUT_S,
    device_id: str | None = None,
) -> LoginResult:
    """Wait for the user to approve ``challenge`` and parse the login result.

    GETs ``challenge.lp_url`` (a long poll, so ``timeout_s`` is generous), with the
    ``deviceId`` cookie when a ``device_id`` is given. Raises
    ``NotificationRequiredError`` when the server demands a security confirmation
    (``securityStatus != 0``) and ``ApiError`` for anything else that is not a
    complete, successful login.
    """
    http = _http_or_default(http)
    what = "QR login long poll"
    response = _get(
        http, challenge.lp_url, timeout=timeout_s, cookies=_device_cookies(device_id)
    )
    payload = _load_json(response, what)
    _raise_for_code(payload, what)

    if (_to_int(payload.get("securityStatus")) or 0) != 0:
        notification_url = _text(payload.get("notificationUrl"))
        if not notification_url:
            raise ApiError(
                f"{what} asked for a security confirmation without saying where.",
                payload={"fields": sorted(payload)},
            )
        if not notification_url.startswith("http"):
            notification_url = ACCOUNT_DOMAIN + notification_url
        raise NotificationRequiredError(notification_url)

    user_id = _text(payload.get("userId"))
    pass_token = _text(payload.get("passToken"))
    c_user_id = _text(payload.get("cUserId"))
    location = _text(payload.get("location"))
    ssecurity = _text(payload.get("ssecurity"))
    nonce = _nonce_text(payload.get("nonce"))
    if not ssecurity or nonce is None:
        # The account SDK falls back to the Extension-Pragma header for these two.
        pragma = _extension_pragma(response)
        ssecurity = ssecurity or _text(pragma.get("ssecurity"))
        if nonce is None:
            nonce = _nonce_text(pragma.get("nonce"))

    missing = [
        name
        for name, present in (
            ("userId", bool(user_id)),
            ("passToken", bool(pass_token)),
            ("cUserId", bool(c_user_id)),
            ("ssecurity", bool(ssecurity)),
            ("nonce", nonce is not None),
            ("location", bool(location)),
        )
        if not present
    ]
    if missing or nonce is None:
        raise _missing_fields_error("QR login result", missing, payload)

    region_hint = next(
        (text for key in _REGION_HINT_KEYS if (text := _text(payload.get(key)))), None
    )
    return LoginResult(
        pass_token=pass_token,
        c_user_id=c_user_id,
        ssecurity=ssecurity,
        nonce=nonce,
        location=location,
        user_id=user_id,
        region_hint=region_hint,
    )


def exchange_sts(
    result: LoginResult, sid: str, http: Any = None, *, device_id: str = ""
) -> Session:
    """Trade a login result for a service token at the STS URL and build a ``Session``.

    GETs ``result.location`` with ``clientSign`` and ``_userIdNeedEncrypt=true``
    without following redirects (the APK reads the token from that first
    response) and without cookies, then reads ``<sid>_serviceToken`` (falling back
    to ``serviceToken``) from the response cookies. Other cookies from that
    response are kept in ``extra_cookies``. The session keeps ``result.pass_token``
    and ``device_id`` so it can be renewed later. The region comes from
    ``result.region_hint`` and defaults to ``cn``. Raises ``ApiError`` on an
    incomplete result, a non-https STS URL, an HTTP error or a response without a
    token.
    """
    nonce = _nonce_text(result.nonce)
    missing = [
        name
        for name, present in (
            ("user_id", bool(result.user_id)),
            ("c_user_id", bool(result.c_user_id)),
            ("ssecurity", bool(result.ssecurity)),
            ("nonce", nonce is not None),
            ("location", bool(result.location)),
        )
        if not present
    ]
    if missing or nonce is None:
        raise ApiError(f"Login result is missing: {', '.join(missing)}.")

    # The signature derived from ssecurity is sent to this URL, so refuse to send it
    # anywhere unencrypted (or anywhere that is not a real host).
    parsed = urlparse(result.location)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ApiError("The STS URL from the login result is not an https URL; refusing to call it.")

    http = _http_or_default(http)
    response = _get(
        http,
        result.location,
        params={
            "clientSign": _client_sign(nonce, result.ssecurity),
            "_userIdNeedEncrypt": "true",
        },
        timeout=_REQUEST_TIMEOUT_S,
        allow_redirects=False,
    )
    status = response.status_code
    if status not in (200, 302):
        raise ApiError(f"STS request failed with HTTP {status}.", payload={"http_status": status})

    cookies = _response_cookies(response)
    sid_token_name = f"{sid}_serviceToken"
    service_token = cookies.get(sid_token_name) or cookies.get("serviceToken")
    if not service_token:
        raise ApiError(
            f"STS response did not include a {sid_token_name} or serviceToken cookie.",
            payload={"cookie_names": sorted(cookies)},
        )
    extra_cookies = {
        name: value
        for name, value in cookies.items()
        if name not in (sid_token_name, "serviceToken")
    }

    return Session(
        user_id=str(result.user_id),
        c_user_id=str(result.c_user_id),
        service_token=service_token,
        ssecurity=result.ssecurity,
        region=_resolve_region(result.region_hint),
        created_at=_utc_now_iso(),
        extra_cookies=extra_cookies,
        pass_token=result.pass_token,
        device_id=device_id,
    )


def login_qr(
    http: Any = None,
    on_qr: Callable[[str, str], None] | None = None,
    *,
    sid: str = SID,
) -> Session:
    """Run the whole QR login and return the resulting ``Session``.

    ``on_qr(login_url, qr_payload)`` is called once the QR session exists and before
    polling starts, so a UI can show the code while the user approves; it is
    optional only for headless callers that obtain the code some other way. One
    ``http`` client is shared by all three requests so cookies persist between them.
    One fresh device id per login goes to both account requests as the ``deviceId``
    cookie and is kept in the session. Blocks until the user approves, the poll
    fails or ``NotificationRequiredError`` says an extra confirmation is needed.
    """
    http = _http_or_default(http)
    device_id = new_device_id()
    challenge = request_qr_login(http, sid, device_id=device_id)
    if on_qr is not None:
        on_qr(challenge.login_url, challenge.qr_payload)
    result = poll_qr_login(challenge, http, device_id=device_id)
    return exchange_sts(result, sid, http, device_id=device_id)
