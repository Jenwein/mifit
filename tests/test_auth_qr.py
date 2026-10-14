"""QR login + STS exchange tests.

A scripted fake ``http`` object stands in for ``requests``; nothing here touches
the network. Every credential, token, URL and id below is synthetic.
"""

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from mifit.auth.qr import (
    LoginResult,
    QrChallenge,
    exchange_sts,
    login_qr,
    poll_qr_login,
    request_qr_login,
)
from mifit.auth.session import Session
from mifit.errors import ApiError, MiFitError, NotificationRequiredError

SAFE_PREFIX = "&&&START&&&"

SYNTH_SID = "miothealth"
SYNTH_USER_ID = "1000001"
SYNTH_C_USER_ID = "synthetic-cuser-id"
SYNTH_PASS_TOKEN = "SYNTH-PASS-TOKEN-0000"
SYNTH_SSECURITY = base64.b64encode(b"synthetic-ssecurity").decode("ascii")
SYNTH_NONCE = 1234567890123456789  # larger than 2**53: must stay an exact integer
SYNTH_SERVICE_TOKEN = "SYNTH-SERVICE-TOKEN-0000"
SYNTH_OTHER_TOKEN = "SYNTH-GENERIC-TOKEN-1111"

LOGIN_URL = "https://account.xiaomi.com/longPolling/loginUrl"
EXPECTED_QR_REQUEST_URL = f"{LOGIN_URL}?sid=miothealth&_json=true"
SYNTH_LP_URL = "https://lp.synthetic.example.com/longPolling/poll?token=synthetic-lp"
SYNTH_LOGIN_URL = "https://account.xiaomi.com/synthetic/qr-confirm?token=synthetic-confirm"
SYNTH_QR = "https://account.xiaomi.com/synthetic/qr-image?token=synthetic-qr"
SYNTH_LOCATION = "https://sts.synthetic.example.com/sts?d=synthetic&ticket=0&pwd=1"
ALL_SECRETS = (
    SYNTH_PASS_TOKEN,
    SYNTH_SSECURITY,
    SYNTH_SERVICE_TOKEN,
    SYNTH_OTHER_TOKEN,
)


def safe_body(payload):
    return SAFE_PREFIX + json.dumps(payload)


def qr_response_payload(**overrides):
    payload = {
        "code": 0,
        "description": "success",
        "lp": SYNTH_LP_URL,
        "loginUrl": SYNTH_LOGIN_URL,
        "qr": SYNTH_QR,
    }
    payload.update(overrides)
    return payload


def poll_response_payload(**overrides):
    payload = {
        "code": 0,
        "desc": "success",
        "userId": int(SYNTH_USER_ID),
        "passToken": SYNTH_PASS_TOKEN,
        "cUserId": SYNTH_C_USER_ID,
        "ssecurity": SYNTH_SSECURITY,
        "nonce": SYNTH_NONCE,
        "psecurity": "synthetic-psecurity",
        "location": SYNTH_LOCATION,
        "securityStatus": 0,
    }
    payload.update(overrides)
    return payload


class FakeResponse:
    def __init__(self, status_code=200, text="", cookies=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.cookies = cookies if cookies is not None else {}
        self.headers = headers if headers is not None else {}


class FakeHttp:
    """Answers each GET with the next scripted response and records the call."""

    def __init__(self, *responses, events=None):
        self._responses = list(responses)
        self.calls = []
        self._events = events

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(
            SimpleNamespace(
                url=url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
        )
        if self._events is not None:
            self._events.append(("get", url))
        if not self._responses:
            raise AssertionError(f"unscripted GET {url}")
        return self._responses.pop(0)


def qr_ok():
    return FakeResponse(text=safe_body(qr_response_payload()))


def poll_ok(**overrides):
    return FakeResponse(text=safe_body(poll_response_payload(**overrides)))


def sts_ok(cookies=None, status=200):
    if cookies is None:
        cookies = {f"{SYNTH_SID}_serviceToken": SYNTH_SERVICE_TOKEN}
    return FakeResponse(status_code=status, cookies=cookies)


def make_challenge():
    return QrChallenge(
        login_url=SYNTH_LOGIN_URL, qr_payload=SYNTH_QR, lp_url=SYNTH_LP_URL
    )


def make_result(**overrides):
    fields = dict(
        pass_token=SYNTH_PASS_TOKEN,
        c_user_id=SYNTH_C_USER_ID,
        ssecurity=SYNTH_SSECURITY,
        nonce=123,
        location=SYNTH_LOCATION,
        user_id=SYNTH_USER_ID,
    )
    fields.update(overrides)
    return LoginResult(**fields)


def expected_client_sign(nonce, ssecurity):
    digest = hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


# --------------------------------------------------------------------------
# request_qr_login
# --------------------------------------------------------------------------


def test_request_qr_hits_longpolling_url():
    http = FakeHttp(qr_ok())

    challenge = request_qr_login(http)

    assert len(http.calls) == 1
    assert http.calls[0].url == EXPECTED_QR_REQUEST_URL
    assert "sid=miothealth" in http.calls[0].url
    assert challenge == QrChallenge(
        login_url=SYNTH_LOGIN_URL, qr_payload=SYNTH_QR, lp_url=SYNTH_LP_URL
    )


def test_request_qr_uses_the_given_sid():
    http = FakeHttp(qr_ok())

    request_qr_login(http, sid="passport")

    assert http.calls[0].url == f"{LOGIN_URL}?sid=passport&_json=true"


def test_request_qr_accepts_a_body_without_the_safe_prefix():
    http = FakeHttp(FakeResponse(text=json.dumps(qr_response_payload())))

    assert request_qr_login(http).lp_url == SYNTH_LP_URL


def test_request_qr_nonzero_code_raises_api_error_with_code():
    body = safe_body({"code": 70016, "description": "synthetic refusal"})
    http = FakeHttp(FakeResponse(text=body))

    with pytest.raises(ApiError) as excinfo:
        request_qr_login(http)

    assert excinfo.value.code == 70016
    assert "synthetic refusal" in str(excinfo.value)


@pytest.mark.parametrize("missing", ["lp", "loginUrl", "qr"])
def test_request_qr_missing_field_raises_api_error(missing):
    payload = qr_response_payload()
    del payload[missing]
    http = FakeHttp(FakeResponse(text=safe_body(payload)))

    with pytest.raises(ApiError) as excinfo:
        request_qr_login(http)

    assert missing in str(excinfo.value)


def test_request_qr_http_error_status_raises_api_error():
    http = FakeHttp(FakeResponse(status_code=503, text="unavailable"))

    with pytest.raises(ApiError) as excinfo:
        request_qr_login(http)

    assert "503" in str(excinfo.value)


def test_request_qr_non_json_body_raises_api_error_without_echoing_it():
    http = FakeHttp(FakeResponse(text="<html>synthetic-body-fragment</html>"))

    with pytest.raises(ApiError) as excinfo:
        request_qr_login(http)

    assert "synthetic-body-fragment" not in str(excinfo.value)


# --------------------------------------------------------------------------
# poll_qr_login
# --------------------------------------------------------------------------


def test_poll_strips_safe_prefix_and_parses():
    http = FakeHttp(poll_ok())

    result = poll_qr_login(make_challenge(), http)

    assert len(http.calls) == 1
    assert http.calls[0].url == SYNTH_LP_URL
    assert result.pass_token == SYNTH_PASS_TOKEN
    assert result.c_user_id == SYNTH_C_USER_ID
    assert result.ssecurity == SYNTH_SSECURITY
    assert result.location == SYNTH_LOCATION
    # A JSON integer userId is normalised to a string; the large nonce stays exact.
    assert result.user_id == SYNTH_USER_ID
    assert str(result.nonce) == str(SYNTH_NONCE)


def test_poll_uses_a_long_read_timeout():
    http = FakeHttp(poll_ok())

    poll_qr_login(make_challenge(), http)

    assert http.calls[0].timeout is not None and http.calls[0].timeout >= 60


def test_poll_accepts_a_body_without_the_safe_prefix():
    body = json.dumps(poll_response_payload())
    http = FakeHttp(FakeResponse(text=body))

    assert poll_qr_login(make_challenge(), http).user_id == SYNTH_USER_ID


def test_poll_nonce_given_as_a_string_is_kept_as_decimal_text():
    http = FakeHttp(poll_ok(nonce=str(SYNTH_NONCE)))

    assert str(poll_qr_login(make_challenge(), http).nonce) == str(SYNTH_NONCE)


def test_poll_zero_nonce_is_accepted():
    http = FakeHttp(poll_ok(nonce=0))

    assert str(poll_qr_login(make_challenge(), http).nonce) == "0"


def test_poll_security_status_nonzero_raises_notification_required():
    http = FakeHttp(
        poll_ok(securityStatus=4, notificationUrl="/identity/authStart?synthetic=1")
    )

    with pytest.raises(NotificationRequiredError) as excinfo:
        poll_qr_login(make_challenge(), http)

    assert (
        excinfo.value.notification_url
        == "https://account.xiaomi.com/identity/authStart?synthetic=1"
    )
    assert isinstance(excinfo.value, MiFitError)


def test_poll_keeps_an_absolute_notification_url_unchanged():
    url = "https://account.xiaomi.com/identity/authStart?synthetic=2"
    http = FakeHttp(poll_ok(securityStatus=1, notificationUrl=url))

    with pytest.raises(NotificationRequiredError) as excinfo:
        poll_qr_login(make_challenge(), http)

    assert excinfo.value.notification_url == url


def test_poll_security_status_without_notification_url_is_an_api_error():
    http = FakeHttp(poll_ok(securityStatus=4))

    with pytest.raises(ApiError):
        poll_qr_login(make_challenge(), http)


def test_poll_nonzero_code_raises_api_error_with_code():
    body = safe_body({"code": 21317, "desc": "synthetic poll failure"})
    http = FakeHttp(FakeResponse(text=body))

    with pytest.raises(ApiError) as excinfo:
        poll_qr_login(make_challenge(), http)

    assert excinfo.value.code == 21317
    assert "synthetic poll failure" in str(excinfo.value)


def test_poll_http_error_status_raises_api_error():
    http = FakeHttp(FakeResponse(status_code=500, text=""))

    with pytest.raises(ApiError) as excinfo:
        poll_qr_login(make_challenge(), http)

    assert "500" in str(excinfo.value)


@pytest.mark.parametrize(
    "missing", ["userId", "passToken", "cUserId", "ssecurity", "nonce", "location"]
)
def test_poll_missing_login_field_raises_api_error_naming_only_the_field(missing):
    payload = poll_response_payload()
    del payload[missing]
    http = FakeHttp(FakeResponse(text=safe_body(payload)))

    with pytest.raises(ApiError) as excinfo:
        poll_qr_login(make_challenge(), http)

    message = str(excinfo.value)
    assert missing in message
    assert not any(secret in message for secret in ALL_SECRETS)
    assert not any(secret in repr(excinfo.value.payload) for secret in ALL_SECRETS)


def test_poll_falls_back_to_the_extension_pragma_header_for_ssecurity_and_nonce():
    payload = poll_response_payload()
    del payload["ssecurity"], payload["nonce"]
    pragma = json.dumps({"ssecurity": SYNTH_SSECURITY, "nonce": SYNTH_NONCE})
    http = FakeHttp(
        FakeResponse(text=safe_body(payload), headers={"Extension-Pragma": pragma})
    )

    result = poll_qr_login(make_challenge(), http)

    assert result.ssecurity == SYNTH_SSECURITY
    assert str(result.nonce) == str(SYNTH_NONCE)


def test_poll_captures_a_region_hint_when_the_login_carries_one():
    assert poll_qr_login(make_challenge(), FakeHttp(poll_ok())).region_hint is None

    http = FakeHttp(poll_ok(userRegion="DE"))
    assert poll_qr_login(make_challenge(), http).region_hint == "DE"


def test_qr_challenge_repr_hides_the_session_bound_polling_url():
    text = repr(make_challenge())

    assert SYNTH_LP_URL not in text
    assert SYNTH_LOGIN_URL in text  # meant to be shown to the user


def test_login_result_repr_hides_secrets():
    text = repr(make_result())

    assert not any(secret in text for secret in ALL_SECRETS)
    assert SYNTH_LOCATION not in text


# --------------------------------------------------------------------------
# exchange_sts
# --------------------------------------------------------------------------


def test_client_sign_formula():
    result = make_result(nonce=123)
    http = FakeHttp(sts_ok())

    exchange_sts(result, SYNTH_SID, http)

    (call,) = http.calls
    assert call.url == SYNTH_LOCATION
    assert call.params["clientSign"] == expected_client_sign(123, SYNTH_SSECURITY)
    assert call.params["_userIdNeedEncrypt"] == "true"


def test_client_sign_uses_the_raw_ssecurity_string_and_decimal_nonce():
    # A nonce > 2**53 and a string-typed nonce must both be signed as exact decimal text.
    for nonce in (SYNTH_NONCE, str(SYNTH_NONCE)):
        http = FakeHttp(sts_ok())

        exchange_sts(make_result(nonce=nonce), SYNTH_SID, http)

        assert http.calls[0].params["clientSign"] == expected_client_sign(
            SYNTH_NONCE, SYNTH_SSECURITY
        )


def test_sts_request_does_not_follow_redirects():
    # The APK reads the token from the first response and never follows Location.
    http = FakeHttp(sts_ok(status=302))

    session = exchange_sts(make_result(), SYNTH_SID, http)

    assert http.calls[0].allow_redirects is False
    assert session.service_token == SYNTH_SERVICE_TOKEN


def test_sts_prefers_the_sid_scoped_cookie_over_the_generic_one():
    cookies = {
        "serviceToken": SYNTH_OTHER_TOKEN,
        f"{SYNTH_SID}_serviceToken": SYNTH_SERVICE_TOKEN,
    }

    session = exchange_sts(make_result(), SYNTH_SID, FakeHttp(sts_ok(cookies)))

    assert session.service_token == SYNTH_SERVICE_TOKEN


def test_sts_falls_back_to_the_generic_service_token_cookie():
    http = FakeHttp(sts_ok({"serviceToken": SYNTH_OTHER_TOKEN}))

    session = exchange_sts(make_result(), SYNTH_SID, http)

    assert session.service_token == SYNTH_OTHER_TOKEN


def test_sts_reads_the_token_from_a_set_cookie_header_when_the_jar_lacks_it():
    header = f"{SYNTH_SID}_serviceToken={SYNTH_SERVICE_TOKEN}; Path=/; HttpOnly"
    http = FakeHttp(FakeResponse(cookies={}, headers={"Set-Cookie": header}))

    session = exchange_sts(make_result(), SYNTH_SID, http)

    assert session.service_token == SYNTH_SERVICE_TOKEN


def test_sts_without_a_service_token_raises_api_error_without_leaking_secrets():
    http = FakeHttp(FakeResponse(cookies={"unrelated": "x"}))

    with pytest.raises(ApiError) as excinfo:
        exchange_sts(make_result(), SYNTH_SID, http)

    message = str(excinfo.value)
    assert "serviceToken" in message
    assert not any(secret in message for secret in ALL_SECRETS)


def test_sts_http_error_status_raises_api_error():
    http = FakeHttp(FakeResponse(status_code=403))

    with pytest.raises(ApiError) as excinfo:
        exchange_sts(make_result(), SYNTH_SID, http)

    assert "403" in str(excinfo.value)


def test_sts_refuses_a_non_https_location_without_sending_anything():
    http = FakeHttp()

    with pytest.raises(ApiError):
        exchange_sts(
            make_result(location="http://sts.synthetic.example.com/sts"),
            SYNTH_SID,
            http,
        )

    assert http.calls == []


def test_sts_builds_a_session_from_the_login_result():
    cookies = {
        f"{SYNTH_SID}_serviceToken": SYNTH_SERVICE_TOKEN,
        f"{SYNTH_SID}_slh": "synthetic-slh",
        "locale": "en_GB",
    }

    session = exchange_sts(make_result(), SYNTH_SID, FakeHttp(sts_ok(cookies)))

    assert isinstance(session, Session)
    assert session.user_id == SYNTH_USER_ID
    assert session.c_user_id == SYNTH_C_USER_ID
    assert session.ssecurity == SYNTH_SSECURITY
    assert session.service_token == SYNTH_SERVICE_TOKEN
    assert session.created_at.endswith("Z")
    # Other STS cookies are kept for the transport; the token itself is not duplicated.
    assert session.extra_cookies == {
        f"{SYNTH_SID}_slh": "synthetic-slh",
        "locale": "en_GB",
    }


@pytest.mark.parametrize(
    "hint, expected",
    [
        (None, "cn"),
        ("", "cn"),
        ("CN", "cn"),
        ("DE", "de"),
        ("us", "us"),
        ("SG", "sg"),
        ("sg", "sg"),  # already a backend region code
        ("ZZ", "cn"),  # unknown -> default
    ],
)
def test_sts_resolves_region_from_the_hint_defaulting_to_cn(hint, expected):
    result = make_result(region_hint=hint)

    session = exchange_sts(result, SYNTH_SID, FakeHttp(sts_ok()))

    assert session.region == expected


def test_sts_region_defaults_to_cn_without_a_hint():
    session = exchange_sts(make_result(), SYNTH_SID, FakeHttp(sts_ok()))

    assert session.region == "cn"


@pytest.mark.parametrize("missing", ["ssecurity", "location", "user_id"])
def test_sts_rejects_an_incomplete_login_result_before_any_request(missing):
    http = FakeHttp()

    with pytest.raises(ApiError):
        exchange_sts(make_result(**{missing: ""}), SYNTH_SID, http)

    assert http.calls == []


# --------------------------------------------------------------------------
# login_qr (orchestration)
# --------------------------------------------------------------------------


def test_login_builds_valid_session():
    http = FakeHttp(qr_ok(), poll_ok(), sts_ok())
    shown = []

    session = login_qr(http, on_qr=lambda login_url, qr: shown.append((login_url, qr)))

    assert shown == [(SYNTH_LOGIN_URL, SYNTH_QR)]
    assert [call.url for call in http.calls] == [
        EXPECTED_QR_REQUEST_URL,
        SYNTH_LP_URL,
        SYNTH_LOCATION,
    ]
    assert session.is_valid()
    assert session.user_id == SYNTH_USER_ID
    assert session.c_user_id == SYNTH_C_USER_ID
    assert session.ssecurity == SYNTH_SSECURITY
    assert session.service_token == SYNTH_SERVICE_TOKEN
    assert session.region == "cn"
    assert http.calls[2].params["clientSign"] == expected_client_sign(
        SYNTH_NONCE, SYNTH_SSECURITY
    )


def test_login_qr_shows_the_qr_before_it_starts_polling():
    events = []
    http = FakeHttp(qr_ok(), poll_ok(), sts_ok(), events=events)

    login_qr(http, on_qr=lambda login_url, qr: events.append(("on_qr", login_url)))

    assert events == [
        ("get", EXPECTED_QR_REQUEST_URL),
        ("on_qr", SYNTH_LOGIN_URL),
        ("get", SYNTH_LP_URL),
        ("get", SYNTH_LOCATION),
    ]


def test_login_qr_stops_at_a_notification_requirement_without_calling_sts():
    http = FakeHttp(
        qr_ok(), poll_ok(securityStatus=4, notificationUrl="/identity/authStart")
    )

    with pytest.raises(NotificationRequiredError):
        login_qr(http, on_qr=lambda *_: None)

    assert len(http.calls) == 2


def test_login_qr_works_without_a_ui_callback():
    http = FakeHttp(qr_ok(), poll_ok(), sts_ok())

    assert login_qr(http).is_valid()


def test_default_http_is_a_requests_session(monkeypatch):
    http = FakeHttp(qr_ok(), poll_ok(), sts_ok())
    created = []

    def fake_session():
        created.append(True)
        return http

    monkeypatch.setattr(requests, "Session", fake_session)

    assert login_qr(on_qr=lambda *_: None).is_valid()
    # One shared session serves all three requests (cookies persist across them).
    assert created == [True]
    assert len(http.calls) == 3
