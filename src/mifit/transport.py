# Portions of this file are adapted from Mi-Fitness-Sync
# (https://github.com/kevinkwee/Mi-Fitness-Sync),
# src/mi_fitness_sync/activity/transport.py,
# used under the MIT License. See THIRD_PARTY_NOTICES for the
# upstream copyright notice and license text.
#
# Upstream: Copyright (c) 2026 Michael Elian Kevin

"""Signed, regional HTTP transport for the Mi Fitness cloud API.

One call = one request: the payload is serialized to compact JSON and sent as
the single signed/encrypted ``data`` query parameter (a request without a payload
sends none), the response body is RC4-decrypted, and success is judged on the
HTTP status first and the decrypted business ``code`` second. The one exception:
with a ``renew`` hook, an HTTP 401 renews the session and sends the request once
more. Requests go under ``app/v1/`` unless a call names another base path.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from mifit.auth.session import Session
from mifit.crypto import decrypt_response_payload, encrypt_query_params, generate_nonce
from mifit.errors import ApiError, AuthError, RenewalError
from mifit.region import BASE_HOST, BASE_PATH, regionalize

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_RENEWALS = 3
"""The Mi Fitness app's own cap on 401 retries (``RETRY_MAX_NUM``)."""
_JITTER_FRACTION = 0.5
"""Up to this fraction of ``delay_s`` is added at random to each pause."""

# A bad signature also produces 401 (with business code 0), so none of these
# claims the session has expired.
_UNAUTHORIZED = (
    "Request was rejected with HTTP 401 (unauthorized). The session "
    "token may have expired or been revoked, or the request signature "
    "may have been refused; log in again if this persists."
)
_SAME_TOKEN_401 = (
    "Request was rejected with HTTP 401 (unauthorized), and renewing the session "
    "returned the same token, so the request was not retried; log in again if "
    "this persists."
)
_STILL_401 = (
    "Request was rejected with HTTP 401 again right after the session was "
    "renewed, so the request signature was probably refused rather than the "
    "session; automatic renewal is now off for this client."
)


@dataclass(frozen=True)
class PagedResult:
    """The items of a cursor-paginated pull and how it ended early, if it did:
    cut short by a page cap, or stalled on an unusable cursor."""

    items: list[dict]
    truncated: bool
    """``True`` when ``max_pages`` stopped the pull although the service had more
    pages: the last page pointed at a fresh cursor, so another request would have
    been sent. ``False`` for every other way a pull ends."""
    stalled: bool = False
    """``True`` when the cursor guard ended the pull: a page reported a truthy
    ``has_more`` but its ``next_key`` was missing or empty, or equal to the cursor
    that request was sent with. The service had more, but there was no cursor to
    ask for it with. Never true together with ``truncated``."""


class Transport:
    """Sends signed GET requests for ``session`` and returns decrypted payloads.

    ``http`` is any object with ``.get(url, params=..., headers=..., timeout=...)``
    returning something with ``.status_code`` and ``.text`` (a ``requests.Session``
    by default). ``delay_s`` plus a random jitter of up to half of ``delay_s`` is
    slept before every request to stay polite to the service (and to avoid a
    metronomic request pattern); set ``delay_s`` to 0 to disable both.

    ``renew`` is an optional hook ``renew(session) -> Session``. When a request
    gets HTTP 401, the transport calls it with the session in use, switches to the
    session it returns and sends the request once more (freshly signed). That
    happens at most once per request and at most ``max_renewals`` times per
    transport, and never before a 401. A renewal that returns the same service
    token is not retried, and a 401 right after a renewal is taken for a refused
    signature, not an expired session; either one switches renewal off for this
    transport. A hook that fails with an ``OSError`` (every ``requests`` error is
    one) is treated as a network failure rather than a refusal: its exception
    propagates and the attempt does not count. The transport cannot tell how far
    the hook got, so an account service that had already accepted the login may
    precede the failure. Without a hook, a 401 raises ``AuthError`` at once.
    """

    def __init__(
        self,
        session: Session,
        *,
        http: Any = None,
        delay_s: float = 1.0,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        renew: Callable[[Session], Session] | None = None,
        max_renewals: int = _DEFAULT_MAX_RENEWALS,
    ) -> None:
        if http is None:
            import requests  # imported lazily so an injected ``http`` needs no requests

            http = requests.Session()
        self._session = session
        self._http = http
        self._delay_s = delay_s
        self._timeout_s = timeout_s
        self._renew = renew
        self._renewals_left = max_renewals

    @property
    def session(self) -> Session:
        """The session requests are signed with; a renewal replaces it."""
        return self._session

    def get_json(
        self,
        endpoint_path: str,
        data_obj: dict | None,
        *,
        sign_path: str | None = None,
        base_path: str = BASE_PATH,
    ) -> dict:
        """GET ``endpoint_path`` with ``data_obj`` signed as the only parameter.

        The URL is ``BASE_HOST + base_path + endpoint_path`` with the session's
        regional host prefix; ``base_path`` defaults to ``app/v1/`` (the profile,
        for one, lives under ``healthapp/``). ``data_obj=None`` sends no ``data``
        parameter at all: the query is only ``rc4_hash__``, ``signature`` and
        ``_nonce``, signed over no parameters. ``sign_path`` overrides the path
        that goes into the signature (the URL itself is unchanged); by default it
        is the URL's path. Both keywords hold for the retry after a renewal too.

        Raises ``AuthError`` on an HTTP 401 that was not renewed away
        (``RenewalError`` when the ``renew`` hook failed), ``ApiError`` on any
        other non-2xx status, an undecryptable body, or a decrypted
        ``code != 0``. An ``OSError`` from ``http`` (every ``requests`` exception
        is one: timeouts, connection errors) propagates unchanged, and so does one
        from the ``renew`` hook.
        """
        response, nonce = self._send(endpoint_path, data_obj, sign_path, base_path)
        if (
            response.status_code == 401
            and self._renew is not None
            and self._renewals_left > 0
        ):
            self._renewals_left -= 1
            old = self._session
            refused: RenewalError | None = None
            try:
                new = self._renew(old)
            except RenewalError as exc:
                # Raised after the block, so the original is not left on
                # ``__context__`` (``from None`` would only hide it from the
                # printed traceback).
                refused = RenewalError(
                    "Request was rejected with HTTP 401 and the session could not "
                    f"be renewed: {exc}",
                    code=exc.code,
                )
            except OSError:
                # A network failure, not a refusal: the attempt is given back.
                self._renewals_left += 1
                raise
            if refused is not None:
                raise refused
            if new.service_token == old.service_token:
                self._renewals_left = 0
                raise AuthError(_SAME_TOKEN_401)
            self._session = new
            # A fresh nonce, signed with the new ssecurity and sent with the new cookies.
            response, nonce = self._send(endpoint_path, data_obj, sign_path, base_path)
            if response.status_code == 401:
                self._renewals_left = 0
                raise AuthError(_STILL_401)

        status = response.status_code
        if status == 401:
            raise AuthError(_UNAUTHORIZED)
        if not 200 <= status < 300:
            raise ApiError(
                f"Request failed with HTTP {status}.", payload={"http_status": status}
            )

        undecodable = False
        try:
            payload = decrypt_response_payload(
                response.text, nonce, self._session.ssecurity
            )
        except ValueError:
            # Raised after the block, so the decode error is not left on
            # ``__context__``: a ``JSONDecodeError`` keeps the whole decrypted
            # document in ``.doc`` (``from None`` would only hide it from the
            # printed traceback).
            undecodable = True
        if undecodable:
            raise ApiError(
                "Response body could not be decrypted or parsed as a JSON object."
            )

        code = payload.get("code")
        if code != 0:
            raise ApiError(
                str(payload.get("message") or "API returned an error."),
                code=code,
                payload=payload,
            )
        return payload

    def get_paged(
        self,
        endpoint_path: str,
        data_obj: dict,
        *,
        list_key: str = "data_list",
        sign_path: str | None = None,
        max_pages: int | None = None,
    ) -> list[dict]:
        """Follow a cursor-paginated endpoint and return all items concatenated.

        Calls ``get_json`` repeatedly, collecting ``list_key`` from each page's
        ``result`` object (falling back to the payload itself when it has no
        ``result``). While a page reports a truthy ``has_more`` the server's
        ``next_key`` is sent back unchanged as ``next_key`` in the next request
        (``data_obj`` is copied per request; the caller's dict is never modified,
        and the first request carries only what the caller put in it).

        Stops when ``has_more`` is falsy, after ``max_pages`` pages (``None`` means
        no cap; ``0`` or less fetches nothing), or when ``has_more`` stays true but
        ``next_key`` is missing/empty or equals the cursor that request was sent
        with, which would otherwise loop forever. The guard ends the pull quietly;
        the items of the page that tripped it are kept.

        Use ``get_paged_result`` to also learn whether ``max_pages`` cut the pull
        short (``truncated``) or the guard ended it (``stalled``).
        """
        return self.get_paged_result(
            endpoint_path,
            data_obj,
            list_key=list_key,
            sign_path=sign_path,
            max_pages=max_pages,
        ).items

    def get_paged_result(
        self,
        endpoint_path: str,
        data_obj: dict,
        *,
        list_key: str = "data_list",
        sign_path: str | None = None,
        max_pages: int | None = None,
    ) -> PagedResult:
        """``get_paged`` that also reports whether the pull ended early, and how.

        Same arguments and stopping rules as ``get_paged``. ``truncated`` is true
        only when the loop ended because ``max_pages`` pages were fetched while the
        last page still reported ``has_more`` with a non-empty ``next_key`` that
        differs from the cursor it was requested with, that is, when one more
        request would have been sent. It is false when the service ran out of
        pages, when the repeated/missing-cursor guard ended the pull, when there
        is no cap, and when ``max_pages`` is 0 or less.

        ``stalled`` is true exactly when that guard ended the pull: a page reported
        a truthy ``has_more`` but its ``next_key`` was missing or empty, or equal
        to the cursor that request was sent with (on the capped page too). The two
        are never both true.
        """
        items: list[dict] = []
        request = dict(data_obj)
        pages = 0
        truncated = False
        stalled = False
        while max_pages is None or pages < max_pages:
            payload = self.get_json(endpoint_path, request, sign_path=sign_path)
            pages += 1
            # The live service nests the business data under ``result``
            # (``{"code": 0, "result": {"data_list": [...], ...}}``); a flat
            # payload is read as is.
            result = payload.get("result")
            body = result if isinstance(result, dict) else payload
            items.extend(body.get(list_key) or [])

            if not body.get("has_more"):
                break
            next_key = body.get("next_key")
            if not next_key or next_key == request.get("next_key"):
                # The service has more but gave no cursor to ask for it with.
                stalled = True
                break
            request = {**data_obj, "next_key": next_key}
            # Reaching the cap here, with a fresh cursor in hand, ends the loop
            # although the service has more.
            truncated = max_pages is not None and pages >= max_pages
        return PagedResult(items, truncated, stalled)

    def _send(
        self,
        endpoint_path: str,
        data_obj: dict | None,
        sign_path: str | None,
        base_path: str,
    ) -> tuple[Any, str]:
        """Sign ``data_obj`` for the current session, pause, GET; return the response and its nonce.

        The URL is ``BASE_HOST + base_path + endpoint_path`` (regionalized for the
        session). A ``data_obj`` of ``None`` sends no ``data`` parameter at all,
        so the signed query holds only the signing fields.
        """
        base_url = BASE_HOST + base_path + endpoint_path
        url = regionalize(base_url, self._session.region)
        nonce = generate_nonce()
        plain = (
            {}
            if data_obj is None
            else {"data": json.dumps(data_obj, separators=(",", ":"))}
        )
        params = encrypt_query_params(
            method="GET",
            path=urlparse(base_url).path,
            params=plain,
            nonce=nonce,
            ssecurity=self._session.ssecurity,
            signature_path=sign_path,
        )

        if self._delay_s > 0:
            time.sleep(
                self._delay_s + random.uniform(0, _JITTER_FRACTION * self._delay_s)
            )
        response = self._http.get(
            url,
            params=params,
            headers=self._headers(),
            timeout=self._timeout_s,
        )
        return response, nonce

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self._cookie_header(),
            "region_tag": self._session.region,
        }

    def _cookie_header(self) -> str:
        session = self._session
        cookies = {
            "serviceToken": session.service_token,
            "cUserId": session.c_user_id,
        }
        if session.user_id:
            cookies["userId"] = session.user_id
        # Only the cookies the app itself sends are forwarded; other
        # ``extra_cookies`` entries stay local and are not sent to the service.
        locale = session.extra_cookies.get("locale") or session.extra_cookies.get(
            "uLocale"
        )
        if locale:
            cookies["locale"] = locale
        return "; ".join(f"{name}={value}" for name, value in cookies.items())
