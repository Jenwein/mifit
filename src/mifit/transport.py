"""Signed, regional HTTP transport for the Mi Fitness cloud API.

One call = one request: the payload is serialized to compact JSON and sent as
the single signed/encrypted ``data`` query parameter, the response body is
RC4-decrypted, and success is judged on the HTTP status first and the decrypted
business ``code`` second.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from mifit.auth.session import Session
from mifit.crypto import decrypt_response_payload, encrypt_query_params, generate_nonce
from mifit.errors import ApiError, AuthError
from mifit.region import BASE_HOST, BASE_PATH, regionalize

_DEFAULT_TIMEOUT_S = 30.0


class Transport:
    """Sends signed GET requests for ``session`` and returns decrypted payloads.

    ``http`` is any object with ``.get(url, params=..., headers=..., timeout=...)``
    returning something with ``.status_code`` and ``.text`` (a ``requests.Session``
    by default). ``delay_s`` is slept before every request to stay polite to the
    service; set it to 0 to disable.
    """

    def __init__(
        self,
        session: Session,
        *,
        http: Any = None,
        delay_s: float = 1.0,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if http is None:
            import requests  # imported lazily so an injected ``http`` needs no requests

            http = requests.Session()
        self._session = session
        self._http = http
        self._delay_s = delay_s
        self._timeout_s = timeout_s

    def get_json(
        self,
        endpoint_path: str,
        data_obj: dict,
        *,
        sign_path: str | None = None,
    ) -> dict:
        """GET ``endpoint_path`` with ``data_obj`` signed as the only parameter.

        ``sign_path`` overrides the path that goes into the signature (the URL
        itself is unchanged). Raises ``AuthError`` on HTTP 401, ``ApiError`` on any
        other non-2xx status, an undecryptable body, or a decrypted ``code != 0``.
        """
        base_url = BASE_HOST + BASE_PATH + endpoint_path
        url = regionalize(base_url, self._session.region)
        nonce = generate_nonce()
        params = encrypt_query_params(
            method="GET",
            path=urlparse(base_url).path,
            params={"data": json.dumps(data_obj, separators=(",", ":"))},
            nonce=nonce,
            ssecurity=self._session.ssecurity,
            signature_path=sign_path,
        )

        if self._delay_s > 0:
            time.sleep(self._delay_s)
        response = self._http.get(
            url,
            params=params,
            headers=self._headers(),
            timeout=self._timeout_s,
        )

        status = response.status_code
        if status == 401:
            # A bad signature also produces 401 (with business code 0), so do not
            # claim the session has expired.
            raise AuthError(
                "Request was rejected with HTTP 401 (unauthorized). The session "
                "token may have expired or been revoked, or the request signature "
                "may have been refused; log in again if this persists."
            )
        if not 200 <= status < 300:
            raise ApiError(
                f"Request failed with HTTP {status}.", payload={"http_status": status}
            )

        try:
            payload = decrypt_response_payload(
                response.text, nonce, self._session.ssecurity
            )
        except ValueError:
            # `from None`: the decode errors can carry fragments of the body.
            raise ApiError(
                "Response body could not be decrypted or parsed as a JSON object."
            ) from None

        code = payload.get("code")
        if code != 0:
            raise ApiError(
                str(payload.get("message") or "API returned an error."),
                code=code,
                payload=payload,
            )
        return payload

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
