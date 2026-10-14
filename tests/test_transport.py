"""Transport tests. A fake ``http`` object stands in for ``requests``; nothing here
touches the network, and all credentials/payloads are synthetic."""

import base64
import json
import random
from urllib.parse import urlparse

import pytest

from mifit import transport as transport_module
from mifit.auth.session import Session
from mifit.crypto import encrypt_query_params, rc4_crypt, signed_nonce
from mifit.errors import ApiError, AuthError, MiFitError
from mifit.transport import Transport

SYNTH_SSECURITY = base64.b64encode(b"synthetic-secret").decode("ascii")
SYNTH_SERVICE_TOKEN = "SYNTH-SERVICE-TOKEN-0000"
ENDPOINT = "data/get_fitness_data_by_time"
SIGN_PATH = "/app/v1/data/get_fitness_data_by_time"
DATA_OBJ = {"sid": "synthetic", "start_time": 1, "end_time": 2}
COMPACT_DATA = '{"sid":"synthetic","start_time":1,"end_time":2}'


def make_session(region="cn", extra_cookies=None):
    return Session(
        user_id="1000001",
        c_user_id="synthetic-cuser",
        service_token=SYNTH_SERVICE_TOKEN,
        ssecurity=SYNTH_SSECURITY,
        region=region,
        created_at="2026-01-01T00:00:00Z",
        extra_cookies=extra_cookies or {},
    )


def encrypt_body(payload, nonce):
    """Build a response body exactly as the server would: b64(RC4(json))."""
    key = base64.b64decode(signed_nonce(SYNTH_SSECURITY, nonce))
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(rc4_crypt(key, raw)).decode("ascii")


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeHttp:
    """Records each GET and answers with ``status`` and a body built from the
    request's own ``_nonce`` (so the body decrypts with the request's nonce)."""

    def __init__(self, status=200, payload=None, raw_body=None):
        self.status = status
        self.payload = {"code": 0, "data_list": []} if payload is None else payload
        self.raw_body = raw_body
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        if self.raw_body is not None:
            body = self.raw_body
        else:
            body = encrypt_body(self.payload, params["_nonce"])
        return FakeResponse(self.status, body)


def make_transport(http, **session_kwargs):
    return Transport(make_session(**session_kwargs), http=http, delay_s=0)


def test_request_is_signed_and_regionalized():
    http = FakeHttp()
    make_transport(http).get_json(ENDPOINT, DATA_OBJ)

    (call,) = http.calls
    assert urlparse(call["url"]).netloc == "hlth.io.mi.com"
    assert urlparse(call["url"]).path == SIGN_PATH
    assert set(call["params"]) == {"data", "rc4_hash__", "signature", "_nonce"}

    # Only `data` is signed/encrypted, over the compact JSON and the URL path.
    expected = encrypt_query_params(
        method="GET",
        path=SIGN_PATH,
        params={"data": COMPACT_DATA},
        nonce=call["params"]["_nonce"],
        ssecurity=SYNTH_SSECURITY,
    )
    assert call["params"] == expected

    cookie = call["headers"]["Cookie"]
    assert f"serviceToken={SYNTH_SERVICE_TOKEN}" in cookie
    assert "cUserId=synthetic-cuser" in cookie
    assert "userId=1000001" in cookie
    assert call["headers"]["region_tag"] == "cn"
    assert call["timeout"] > 0


def test_non_cn_region_prefixes_host_and_sets_region_tag():
    http = FakeHttp()
    make_transport(http, region="de").get_json(ENDPOINT, DATA_OBJ)

    (call,) = http.calls
    assert urlparse(call["url"]).netloc == "de.hlth.io.mi.com"
    assert urlparse(call["url"]).path == SIGN_PATH
    assert call["headers"]["region_tag"] == "de"
    # Signing path is the URL path, not affected by the host rewrite.
    expected = encrypt_query_params(
        method="GET",
        path=SIGN_PATH,
        params={"data": COMPACT_DATA},
        nonce=call["params"]["_nonce"],
        ssecurity=SYNTH_SSECURITY,
    )
    assert call["params"] == expected


def test_sign_path_overrides_signing_path_only():
    http = FakeHttp()
    make_transport(http).get_json(ENDPOINT, DATA_OBJ, sign_path="/stripped/path")

    (call,) = http.calls
    assert urlparse(call["url"]).path == SIGN_PATH  # URL unchanged
    expected = encrypt_query_params(
        method="GET",
        path=SIGN_PATH,
        params={"data": COMPACT_DATA},
        nonce=call["params"]["_nonce"],
        ssecurity=SYNTH_SSECURITY,
        signature_path="/stripped/path",
    )
    assert call["params"] == expected
    default_signed = encrypt_query_params(
        method="GET",
        path=SIGN_PATH,
        params={"data": COMPACT_DATA},
        nonce=call["params"]["_nonce"],
        ssecurity=SYNTH_SSECURITY,
    )
    assert call["params"]["signature"] != default_signed["signature"]


def test_each_request_uses_a_fresh_nonce():
    http = FakeHttp()
    transport = make_transport(http)
    transport.get_json(ENDPOINT, DATA_OBJ)
    transport.get_json(ENDPOINT, DATA_OBJ)
    assert http.calls[0]["params"]["_nonce"] != http.calls[1]["params"]["_nonce"]


def test_locale_cookie_sent_when_available_and_other_extras_not_forwarded():
    http = FakeHttp()
    make_transport(
        http, extra_cookies={"locale": "en_US", "deviceId": "SYNTH-DEVICE"}
    ).get_json(ENDPOINT, DATA_OBJ)
    cookie = http.calls[0]["headers"]["Cookie"]
    assert "locale=en_US" in cookie
    assert "SYNTH-DEVICE" not in cookie


def test_locale_cookie_falls_back_to_uLocale_and_is_omitted_when_absent():
    http = FakeHttp()
    make_transport(http, extra_cookies={"uLocale": "de_DE"}).get_json(ENDPOINT, DATA_OBJ)
    assert "locale=de_DE" in http.calls[0]["headers"]["Cookie"]

    http = FakeHttp()
    make_transport(http).get_json(ENDPOINT, DATA_OBJ)
    assert "locale=" not in http.calls[0]["headers"]["Cookie"]


def test_http_401_raises_AuthError():
    # Even with a body that decrypts to code=0: HTTP status is judged first.
    http = FakeHttp(status=401, payload={"code": 0})
    with pytest.raises(AuthError) as excinfo:
        make_transport(http).get_json(ENDPOINT, DATA_OBJ)

    message = str(excinfo.value).lower()
    assert "401" in message
    # A signing error also yields 401, so expiry must not be asserted as fact.
    assert "may" in message
    assert "has expired" not in message and "is expired" not in message
    assert not isinstance(excinfo.value, ApiError)
    assert isinstance(excinfo.value, MiFitError)


def test_other_http_error_raises_ApiError():
    http = FakeHttp(status=500, payload={"code": 0})
    with pytest.raises(ApiError) as excinfo:
        make_transport(http).get_json(ENDPOINT, DATA_OBJ)
    assert not isinstance(excinfo.value, AuthError)
    assert "500" in str(excinfo.value)
    assert excinfo.value.payload == {"http_status": 500}


def test_business_code_nonzero_raises_ApiError():
    http = FakeHttp(payload={"code": 1, "message": "x"})
    with pytest.raises(ApiError) as excinfo:
        make_transport(http).get_json(ENDPOINT, DATA_OBJ)
    assert excinfo.value.code == 1
    assert "x" in str(excinfo.value)
    assert excinfo.value.payload == {"code": 1, "message": "x"}


def test_success_returns_decrypted_payload():
    http = FakeHttp(payload={"code": 0, "data_list": []})
    result = make_transport(http).get_json(ENDPOINT, DATA_OBJ)
    assert result == {"code": 0, "data_list": []}


def test_undecryptable_body_raises_ApiError_without_leaking_body():
    http = FakeHttp(raw_body="bm90LXZhbGlkLWNpcGhlcnRleHQ=")
    with pytest.raises(ApiError) as excinfo:
        make_transport(http).get_json(ENDPOINT, DATA_OBJ)
    assert "bm90LXZhbGlk" not in str(excinfo.value)


def test_delay_is_applied_before_the_request(monkeypatch):
    events = []
    monkeypatch.setattr(
        transport_module.time, "sleep", lambda seconds: events.append(("sleep", seconds))
    )
    # No jitter, so the slept amount is exactly ``delay_s``.
    monkeypatch.setattr(random, "uniform", lambda low, high: 0.0)
    http = FakeHttp()
    original_get = http.get

    def recording_get(*args, **kwargs):
        events.append(("get", None))
        return original_get(*args, **kwargs)

    http.get = recording_get
    Transport(make_session(), http=http, delay_s=1.5).get_json(ENDPOINT, DATA_OBJ)
    assert events == [("sleep", 1.5), ("get", None)]


def test_delay_has_bounded_jitter(monkeypatch):
    slept = []
    bounds = []

    def fake_uniform(low, high):
        bounds.append((low, high))
        return high  # the largest jitter the transport allows

    monkeypatch.setattr(transport_module.time, "sleep", slept.append)
    monkeypatch.setattr(random, "uniform", fake_uniform)

    Transport(make_session(), http=FakeHttp(), delay_s=2.0).get_json(ENDPOINT, DATA_OBJ)

    # Jitter is drawn from 0..0.5*delay_s and added on top of the base delay.
    assert bounds == [(0, 1.0)]
    (seconds,) = slept
    assert 2.0 <= seconds <= 2.0 + 1.0
    assert seconds == 3.0


def test_no_sleep_when_delay_is_zero(monkeypatch):
    def fail(_seconds):
        raise AssertionError("sleep must not be called when delay_s == 0")

    def fail_uniform(_low, _high):
        raise AssertionError("no jitter is drawn when delay_s == 0")

    monkeypatch.setattr(transport_module.time, "sleep", fail)
    monkeypatch.setattr(random, "uniform", fail_uniform)
    make_transport(FakeHttp()).get_json(ENDPOINT, DATA_OBJ)


def test_default_http_is_a_requests_session():
    requests = pytest.importorskip("requests")
    transport = Transport(make_session(), delay_s=0)
    assert isinstance(transport._http, requests.Session)
