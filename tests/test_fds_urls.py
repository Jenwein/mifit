"""The ``gen_download_url`` request and its answer (spec §3, §4.3): one signed request
for several files, answered as a map from server key to a link entry."""

import pytest
from fds_fixtures import (
    ABSENT,
    EXPIRES,
    SYNTH_KEY_B64,
    SYNTH_SID,
    URL_MARK,
    FakeFdsService,
    FdsTransport,
)

from mifit.errors import ApiError, AuthError
from mifit.fds.ids import GPS, RECORD, FileRef, server_key, suffix
from mifit.fds.urls import FdsEntry, request_urls

T = 1700000000
REFS = (
    FileRef(SYNTH_SID, T, 32, 22, RECORD),
    FileRef(SYNTH_SID, T, 32, 22, GPS),
)
RECORD_KEY = server_key(REFS[0])
GPS_KEY = server_key(REFS[1])


def _entries(service, refs=REFS):
    return request_urls(FdsTransport([], service), SYNTH_SID, refs)


def test_request_shape_follows_the_signing_rule():
    transport = FdsTransport([], FakeFdsService())

    request_urls(transport, SYNTH_SID, REFS)

    assert transport.link_calls == [
        {
            "endpoint_path": "service/gen_download_url",
            "data_obj": {
                "did": SYNTH_SID,
                "items": [
                    {"timestamp": T, "suffix": suffix(REFS[0])},
                    {"timestamp": T, "suffix": suffix(REFS[1])},
                ],
            },
            "sign_path": "/service/gen_download_url",
            "base_path": "healthapp/",
        }
    ]


def test_entries_are_keyed_by_server_key():
    entries = _entries(FakeFdsService({RECORD_KEY: b"plain record"}))

    assert set(entries) == {RECORD_KEY, GPS_KEY}
    record = entries[RECORD_KEY]
    assert record.key_b64 == SYNTH_KEY_B64
    assert record.method == "GET"
    assert record.expires_s == EXPIRES
    assert URL_MARK in record.url
    # The file does not exist: the entry is there, without a key (spec §4.3).
    assert entries[GPS_KEY].key_b64 is None


@pytest.mark.parametrize(
    "override",
    [{"url": ABSENT}, {"url": None}, {"url": 12345}, ["not", "an", "entry"]],
    ids=["url-absent", "url-none", "url-number", "list"],
)
def test_malformed_entries_are_left_out(override):
    entries = _entries(FakeFdsService(entry_overrides={RECORD_KEY: override}))

    assert RECORD_KEY not in entries
    assert GPS_KEY in entries


@pytest.mark.parametrize(
    "obj_key", [ABSENT, "", None, 12345], ids=["absent", "empty", "none", "number"]
)
def test_unusable_key_means_no_file(obj_key):
    overrides = {RECORD_KEY: {"obj_key": obj_key}}
    service = FakeFdsService({RECORD_KEY: b"plain"}, entry_overrides=overrides)

    assert _entries(service)[RECORD_KEY].key_b64 is None


def test_unusable_method_and_expiry_become_none():
    overrides = {
        RECORD_KEY: {"method": 1, "expires_time": "soon"},
        GPS_KEY: {"method": ABSENT, "expires_time": ABSENT},
    }

    entries = _entries(FakeFdsService(entry_overrides=overrides))

    for key in (RECORD_KEY, GPS_KEY):
        assert entries[key].method is None
        assert entries[key].expires_s is None


@pytest.mark.parametrize("result", [[], None, "map", 0], ids=["list", "none", "str", "zero"])
def test_answer_without_a_file_map_raises_api_error(result):
    class Answer(FakeFdsService):
        def answer(self, data_obj):
            return result

    with pytest.raises(ApiError) as excinfo:
        _entries(Answer())

    assert str(excinfo.value) == "The download-link answer carried no file map."
    assert excinfo.value.payload is None


@pytest.mark.parametrize(
    "error",
    [ApiError("Request failed with HTTP 500."), AuthError("x"), ConnectionError("down")],
    ids=["api", "auth", "os"],
)
def test_link_request_errors_propagate(error):
    transport = FdsTransport([], FakeFdsService(), link_errors={0: error})

    with pytest.raises(type(error)) as excinfo:
        request_urls(transport, SYNTH_SID, REFS)

    assert excinfo.value is error


def test_entry_repr_hides_url_and_key():
    entry = _entries(FakeFdsService({RECORD_KEY: b"plain"}))[RECORD_KEY]

    for text in (repr(entry), str(entry)):
        assert URL_MARK not in text
        assert "SYNTH-ACCESS" not in text
        assert "synthetic-bucket" not in text
        assert str(EXPIRES) not in text
        assert SYNTH_KEY_B64 not in text
        assert "cnbj1.fds.api.xiaomi.com" in text
    assert repr(entry) == "FdsEntry(host='cnbj1.fds.api.xiaomi.com', method='GET', has_key=True)"


def test_entry_repr_survives_a_malformed_url():
    entry = FdsEntry(f"https://[{URL_MARK}/x", None, None, None)

    assert repr(entry) == "FdsEntry(host=None, method=None, has_key=False)"
