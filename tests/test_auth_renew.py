"""Session renewal tests: passToken -> serviceLogin -> STS.

A scripted fake ``http`` object stands in for ``requests``; nothing here touches
the network. Every credential, token, URL and id below is synthetic.
"""

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from mifit.auth.renew import RENEW_URL, renew_session
from mifit.auth.session import Session
from mifit.errors import AuthError, NotificationRequiredError, RenewalError

SAFE_PREFIX = "&&&START&&&"

SYNTH_SID = "miothealth"
SYNTH_USER_ID = "1000001"
SYNTH_C_USER_ID = "synthetic-cuser-id"
SYNTH_DEVICE_ID = "SYNTHDEVICE00001"
OLD_PASS_TOKEN = "SYNTH-OLD-PASS-TOKEN-0000"
OLD_SERVICE_TOKEN = "SYNTH-OLD-SERVICE-TOKEN-0000"
OLD_SSECURITY = base64.b64encode(b"synthetic-old-ssecurity").decode("ascii")
NEW_PASS_TOKEN = "SYNTH-NEW-PASS-TOKEN-1111"
NEW_SERVICE_TOKEN = "SYNTH-NEW-SERVICE-TOKEN-1111"
NEW_SSECURITY = base64.b64encode(b"synthetic-new-ssecurity").decode("ascii")
NEW_NONCE = 9876543210987654321  # larger than 2**53: must stay an exact integer
SYNTH_LOCATION = "https://sts.synthetic.example.com/sts?d=synthetic-renew&ticket=0"
OLD_CREATED_AT = "2026-10-14T03:18:19Z"
ALL_SECRETS = (
    OLD_PASS_TOKEN,
    OLD_SERVICE_TOKEN,
    OLD_SSECURITY,
    NEW_PASS_TOKEN,
    NEW_SERVICE_TOKEN,
    NEW_SSECURITY,
    SYNTH_DEVICE_ID,
    SYNTH_LOCATION,
)


def safe_body(payload):
    return SAFE_PREFIX + json.dumps(payload)


def login_payload(**overrides):
    payload = {
        "code": 0,
        "desc": "success",
        "userId": int(SYNTH_USER_ID),
        "passToken": NEW_PASS_TOKEN,
        "cUserId": SYNTH_C_USER_ID,
        "ssecurity": NEW_SSECURITY,
        "nonce": NEW_NONCE,
        "location": SYNTH_LOCATION,
        "securityStatus": 0,
    }
    payload.update(overrides)
    return payload


class FakeRawHeaders:
    """Mimics urllib3's header dict: one entry per ``Set-Cookie`` line."""

    def __init__(self, set_cookies):
        self._set_cookies = list(set_cookies)

    def getlist(self, name):
        return list(self._set_cookies) if name.lower() == "set-cookie" else []


class FakeResponse:
    def __init__(self, status_code=200, text="", cookies=None, headers=None, set_cookies=()):
        self.status_code = status_code
        self.text = text
        self.cookies = cookies if cookies is not None else {}
        self.headers = headers if headers is not None else {}
        self.raw = SimpleNamespace(headers=FakeRawHeaders(set_cookies))


class FakeHttp:
    """Answers each GET with the next scripted response and records the call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(
        self,
        url,
        params=None,
        headers=None,
        timeout=None,
        allow_redirects=True,
        cookies=None,
    ):
        self.calls.append(
            SimpleNamespace(
                url=url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
                cookies=cookies,
            )
        )
        if not self._responses:
            raise AssertionError(f"unscripted GET {url}")
        return self._responses.pop(0)


def login_ok(**overrides):
    return FakeResponse(text=safe_body(login_payload(**overrides)))


def sts_ok(cookies=None):
    if cookies is None:
        cookies = {f"{SYNTH_SID}_serviceToken": NEW_SERVICE_TOKEN}
    return FakeResponse(cookies=cookies)


def old_session(**overrides):
    fields = dict(
        user_id=SYNTH_USER_ID,
        c_user_id=SYNTH_C_USER_ID,
        service_token=OLD_SERVICE_TOKEN,
        ssecurity=OLD_SSECURITY,
        region="de",
        created_at=OLD_CREATED_AT,
        extra_cookies={"cUserId": SYNTH_C_USER_ID, "userId": SYNTH_USER_ID},
        pass_token=OLD_PASS_TOKEN,
        device_id=SYNTH_DEVICE_ID,
    )
    fields.update(overrides)
    return Session(**fields)


def exception_chain(exc):
    """``exc`` and every exception reachable from it through ``__cause__``/``__context__``."""
    seen = []
    pending = [exc]
    while pending:
        link = pending.pop()
        if link is None or any(link is s for s in seen):
            continue
        seen.append(link)
        pending += [link.__cause__, link.__context__]
    return seen


def assert_stands_alone(exc):
    """Nothing is reachable from ``exc`` but itself: the original failure (whose
    payload or ``JSONDecodeError.doc`` can hold the response body) is not chained."""
    assert exc.__cause__ is None and exc.__context__ is None
    assert exception_chain(exc) == [exc]


def expected_client_sign(nonce, ssecurity):
    digest = hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


def test_renew_calls_service_login_with_pass_token_cookies():
    http = FakeHttp(login_ok(), sts_ok())

    renew_session(old_session(), http)

    assert RENEW_URL == "https://account.xiaomi.com/pass/serviceLogin"
    assert len(http.calls) == 2
    login, sts = http.calls
    assert login.url == RENEW_URL
    assert login.params == {"sid": SYNTH_SID, "_json": "true"}
    assert login.cookies == {
        "userId": SYNTH_USER_ID,
        "passToken": OLD_PASS_TOKEN,
        "deviceId": SYNTH_DEVICE_ID,
    }
    assert login.allow_redirects is False
    assert login.timeout == 30
    # Call 2 is the STS exchange, signed with the *new* nonce and ssecurity.
    assert sts.url == SYNTH_LOCATION
    assert sts.params["clientSign"] == expected_client_sign(NEW_NONCE, NEW_SSECURITY)
    assert sts.allow_redirects is False
    assert sts.cookies is None


def test_renew_without_a_device_id_sends_no_device_id_cookie():
    http = FakeHttp(login_ok(), sts_ok())

    renewed = renew_session(old_session(device_id=""), http)

    assert http.calls[0].cookies == {"userId": SYNTH_USER_ID, "passToken": OLD_PASS_TOKEN}
    assert renewed.device_id == ""


def test_renew_uses_the_given_sid():
    http = FakeHttp(login_ok(), sts_ok({"passport_serviceToken": NEW_SERVICE_TOKEN}))

    renewed = renew_session(old_session(), http, sid="passport")

    assert http.calls[0].params == {"sid": "passport", "_json": "true"}
    assert renewed.service_token == NEW_SERVICE_TOKEN


# --------------------------------------------------------------------------
# the renewed session
# --------------------------------------------------------------------------


def test_renew_returns_a_session_with_the_new_token_and_ssecurity():
    renewed = renew_session(old_session(), FakeHttp(login_ok(), sts_ok()))

    assert isinstance(renewed, Session)
    assert renewed.service_token == NEW_SERVICE_TOKEN
    assert renewed.ssecurity == NEW_SSECURITY
    assert renewed.user_id == SYNTH_USER_ID
    assert renewed.c_user_id == SYNTH_C_USER_ID
    assert renewed.is_valid()
    assert renewed.can_renew()


def test_renew_keeps_region_login_time_and_device_id():
    sts_cookies = {
        f"{SYNTH_SID}_serviceToken": NEW_SERVICE_TOKEN,
        f"{SYNTH_SID}_slh": "synthetic-new-slh",
        "locale": "en_GB",
    }
    old = old_session()

    renewed = renew_session(old, FakeHttp(login_ok(), sts_ok(sts_cookies)))

    assert renewed.region == "de"
    assert renewed.created_at == OLD_CREATED_AT
    assert renewed.device_id == SYNTH_DEVICE_ID
    assert renewed.renewed_at is not None and renewed.renewed_at.endswith("Z")
    assert renewed.renewed_at != OLD_CREATED_AT
    assert renewed.extra_cookies == {
        f"{SYNTH_SID}_slh": "synthetic-new-slh",
        "locale": "en_GB",
    }
    # The old session object is left as it was.
    assert old.service_token == OLD_SERVICE_TOKEN
    assert old.renewed_at is None


def test_renew_stores_a_rotated_pass_token():
    renewed = renew_session(old_session(), FakeHttp(login_ok(), sts_ok()))

    assert renewed.pass_token == NEW_PASS_TOKEN


def test_renew_keeps_the_old_pass_token_when_none_comes_back():
    payload = login_payload()
    del payload["passToken"]
    http = FakeHttp(FakeResponse(text=safe_body(payload)), sts_ok())

    renewed = renew_session(old_session(), http)

    assert renewed.pass_token == OLD_PASS_TOKEN


def test_renew_reads_pass_token_and_user_id_from_cookies():
    payload = login_payload()
    del payload["passToken"], payload["userId"], payload["cUserId"]
    response = FakeResponse(
        text=safe_body(payload),
        set_cookies=[
            f"userId={SYNTH_USER_ID}; Domain=.account.xiaomi.com; Path=/",
            f"passToken={NEW_PASS_TOKEN}; Domain=.account.xiaomi.com; Path=/; HttpOnly",
            "cUserId=synthetic-cuser-from-cookie; Domain=.account.xiaomi.com; Path=/",
        ],
    )

    renewed = renew_session(old_session(), FakeHttp(response, sts_ok()))

    # The cookies win over the stored values (the stored passToken is the old one).
    assert renewed.pass_token == NEW_PASS_TOKEN
    assert renewed.c_user_id == "synthetic-cuser-from-cookie"
    assert renewed.user_id == SYNTH_USER_ID


def test_renew_falls_back_to_extension_pragma_for_ssecurity_and_nonce():
    payload = login_payload()
    del payload["ssecurity"], payload["nonce"]
    pragma = json.dumps({"ssecurity": NEW_SSECURITY, "nonce": NEW_NONCE})
    response = FakeResponse(text=safe_body(payload), headers={"Extension-Pragma": pragma})
    http = FakeHttp(response, sts_ok())

    renewed = renew_session(old_session(), http)

    assert renewed.ssecurity == NEW_SSECURITY
    assert http.calls[1].params["clientSign"] == expected_client_sign(
        NEW_NONCE, NEW_SSECURITY
    )


# --------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------


def test_session_without_pass_token_raises_before_any_request():
    http = FakeHttp()

    with pytest.raises(RenewalError) as excinfo:
        renew_session(old_session(pass_token=""), http)

    assert http.calls == []
    assert "passToken" in str(excinfo.value)


def test_session_without_user_id_raises_before_any_request():
    # ``can_renew()`` is the gate: the userId cookie is as necessary as the passToken.
    http = FakeHttp()

    with pytest.raises(RenewalError):
        renew_session(old_session(user_id=""), http)

    assert http.calls == []


def test_refused_pass_token_raises_renewal_error_with_code():
    body = safe_body({"code": 70016, "desc": "synthetic refusal"})
    http = FakeHttp(FakeResponse(text=body))

    with pytest.raises(RenewalError) as excinfo:
        renew_session(old_session(), http)

    exc = excinfo.value
    assert exc.code == 70016
    assert isinstance(exc, AuthError)
    assert "70016" in str(exc)
    assert_stands_alone(exc)
    assert len(http.calls) == 1


def test_security_confirmation_raises_renewal_error():
    http = FakeHttp(
        login_ok(securityStatus=1, notificationUrl="/identity/authStart?synthetic=1")
    )

    with pytest.raises(RenewalError) as excinfo:
        renew_session(old_session(), http)

    assert not isinstance(excinfo.value, NotificationRequiredError)
    assert_stands_alone(excinfo.value)
    assert "authStart" not in str(excinfo.value)
    assert len(http.calls) == 1


@pytest.mark.parametrize("status", [302, 403, 500])
def test_redirect_or_http_error_raises_renewal_error(status):
    headers = {"Location": SYNTH_LOCATION} if status == 302 else {}
    http = FakeHttp(FakeResponse(status_code=status, headers=headers))

    with pytest.raises(RenewalError) as excinfo:
        renew_session(old_session(), http)

    assert str(status) in str(excinfo.value)
    assert_stands_alone(excinfo.value)
    assert len(http.calls) == 1


def test_sts_failure_raises_renewal_error():
    http = FakeHttp(login_ok(), sts_ok({"unrelated": "x"}))

    with pytest.raises(RenewalError) as excinfo:
        renew_session(old_session(), http)

    assert "serviceToken" in str(excinfo.value)
    assert_stands_alone(excinfo.value)
    assert len(http.calls) == 2


FAILURES = {
    "no pass token": (dict(pass_token=""), ()),
    "refused": ((), (FakeResponse(text=safe_body({"code": 70016, "desc": "x"})),)),
    "security": ((), (login_ok(securityStatus=1, notificationUrl="/identity/x"),)),
    "redirect": ((), (FakeResponse(status_code=302, headers={"Location": SYNTH_LOCATION}),)),
    "forbidden": ((), (FakeResponse(status_code=403),)),
    "server error": ((), (FakeResponse(status_code=500),)),
    "not json": ((), (FakeResponse(text=f"<html>{NEW_PASS_TOKEN}</html>"),)),
    "missing field": ((), (login_ok(ssecurity="", location=""),)),
    "sts failure": ((), (login_ok(), sts_ok({"unrelated": NEW_SERVICE_TOKEN}))),
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_renewal_errors_never_contain_secrets(name):
    overrides, responses = FAILURES[name]
    session = old_session(**dict(overrides))

    with pytest.raises(RenewalError) as excinfo:
        renew_session(session, FakeHttp(*responses))

    # Every link of the chain, attributes included: a JSONDecodeError keeps the
    # whole response body in ``.doc``.
    for link in exception_chain(excinfo.value):
        text = str(link) + repr(link) + repr(link.args) + repr(vars(link))
        assert not any(secret in text for secret in ALL_SECRETS), type(link).__name__
    assert_stands_alone(excinfo.value)


def test_network_errors_propagate():
    error = requests.ConnectionError("synthetic connection failure")

    class FailingHttp:
        def get(self, url, **kwargs):
            raise error

    with pytest.raises(requests.ConnectionError) as excinfo:
        renew_session(old_session(), FailingHttp())

    assert excinfo.value is error
