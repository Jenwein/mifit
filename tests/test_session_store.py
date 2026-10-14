"""Tests for the Session model and the secure local token store.

All values here are synthetic; no real credentials or health data.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from mifit.auth.session import Session, load_session, save_session
from mifit.paths import default_session_path

SYNTH_SERVICE_TOKEN = "SYNTH-SERVICE-TOKEN-0123456789"
SYNTH_SSECURITY = "SYNTH-SSECURITY-ABCDEFGHIJ"
SYNTH_COOKIE_VALUE = "SYNTH-COOKIE-VALUE-QRSTUV"
SYNTH_PASS_TOKEN = "SYNTH-PASS-TOKEN"
SYNTH_DEVICE_ID = "SYNTHDEVICE00001"
SYNTH_RENEWED_AT = "2026-10-15T00:00:00Z"


def make_session(**overrides) -> Session:
    fields = dict(
        user_id="1000000001",
        c_user_id="2000000002",
        service_token=SYNTH_SERVICE_TOKEN,
        ssecurity=SYNTH_SSECURITY,
        region="de",
        created_at="2026-01-01T00:00:00+00:00",
        extra_cookies={"deviceId": SYNTH_COOKIE_VALUE},
    )
    fields.update(overrides)
    return Session(**fields)


def test_save_then_load_roundtrip(tmp_path):
    session = make_session()
    path = tmp_path / "session.json"

    returned = save_session(session, path)
    loaded = load_session(path)

    assert returned == path
    assert loaded == session


def test_roundtrip_keeps_renewal_fields(tmp_path):
    session = make_session(
        pass_token=SYNTH_PASS_TOKEN,
        device_id=SYNTH_DEVICE_ID,
        renewed_at=SYNTH_RENEWED_AT,
    )
    path = tmp_path / "session.json"

    save_session(session, path)
    loaded = load_session(path)

    assert loaded == session
    assert loaded.pass_token == SYNTH_PASS_TOKEN
    assert loaded.device_id == SYNTH_DEVICE_ID
    assert loaded.renewed_at == SYNTH_RENEWED_AT


def test_load_session_saved_before_renewal_fields_existed(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "user_id": "1000000001",
                "c_user_id": "2000000002",
                "service_token": SYNTH_SERVICE_TOKEN,
                "ssecurity": SYNTH_SSECURITY,
                "region": "de",
                "created_at": "2026-01-01T00:00:00Z",
                "extra_cookies": {"userId": "1000000001"},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_session(path)

    assert loaded is not None
    assert loaded.user_id == "1000000001"
    assert loaded.extra_cookies == {"userId": "1000000001"}
    assert loaded.pass_token == ""
    assert loaded.device_id == ""
    assert loaded.renewed_at is None
    assert not loaded.can_renew()


def test_save_creates_missing_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "session.json"

    save_session(make_session(), path)

    assert path.is_file()
    assert load_session(path) == make_session()


def test_save_overwrites_existing_file(tmp_path):
    path = tmp_path / "session.json"
    save_session(make_session(region="de"), path)

    save_session(make_session(region="us"), path)

    assert load_session(path).region == "us"
    # No leftover temp files from the atomic write.
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]


def test_load_missing_returns_none(tmp_path):
    assert load_session(tmp_path / "does-not-exist.json") is None


def test_load_corrupt_file_raises_value_error(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_session(path)


def test_load_file_missing_fields_raises_value_error(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"user_id": "1"}), encoding="utf-8")

    with pytest.raises(ValueError, match="service_token"):
        load_session(path)


def test_saved_file_is_owner_only(tmp_path):
    path = tmp_path / "session.json"

    save_session(make_session(), path)

    assert path.exists()
    if os.name == "nt":
        # Windows has no POSIX permission bits; os.stat() mode is not meaningful.
        return
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_overwrite_keeps_owner_only(tmp_path):
    path = tmp_path / "session.json"
    save_session(make_session(), path)

    save_session(make_session(region="us"), path)

    assert path.exists()
    if os.name == "nt":
        return
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_repr_redacts_secrets():
    session = make_session()

    text = repr(session)

    assert SYNTH_SERVICE_TOKEN not in text
    assert SYNTH_SSECURITY not in text
    assert "<redacted>" in text


def test_str_and_format_redact_secrets():
    session = make_session()

    for text in (str(session), f"{session}", "%s" % session, "%r" % session):
        assert SYNTH_SERVICE_TOKEN not in text
        assert SYNTH_SSECURITY not in text


def test_repr_redacts_pass_token_and_device_id():
    session = make_session(
        pass_token=SYNTH_PASS_TOKEN,
        device_id=SYNTH_DEVICE_ID,
        renewed_at=SYNTH_RENEWED_AT,
    )

    for text in (repr(session), str(session), f"{session}", "%s" % session):
        assert SYNTH_PASS_TOKEN not in text
        assert SYNTH_DEVICE_ID not in text
        assert SYNTH_RENEWED_AT in text


def test_repr_does_not_leak_extra_cookie_values():
    session = make_session()

    assert SYNTH_COOKIE_VALUE not in repr(session)


def test_repr_keeps_non_secret_fields_visible():
    session = make_session()

    text = repr(session)

    assert "1000000001" in text
    assert "region='de'" in text


def test_is_valid_requires_service_token_and_user_id():
    assert make_session().is_valid()
    assert not make_session(service_token="").is_valid()
    assert not make_session(user_id="").is_valid()


@pytest.mark.parametrize(
    "pass_token, user_id, expected",
    [
        (SYNTH_PASS_TOKEN, "1000000001", True),
        ("", "1000000001", False),
        (SYNTH_PASS_TOKEN, "", False),
        ("", "", False),
    ],
)
def test_can_renew_needs_pass_token_and_user_id(pass_token, user_id, expected):
    session = make_session(pass_token=pass_token, user_id=user_id)

    assert session.can_renew() is expected


def test_renewal_fields_default_to_a_session_that_cannot_renew():
    session = make_session()

    assert session.pass_token == ""
    assert session.device_id == ""
    assert session.renewed_at is None
    assert not session.can_renew()


def test_default_session_path_is_under_gitignored_dot_mifit_dir():
    path = default_session_path()

    assert isinstance(path, Path)
    assert path.is_absolute()
    assert path.parent.name == ".mifit"
    assert path.name == "session.json"
