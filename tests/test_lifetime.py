"""Session lifetime observation tests: ages, the describe copy and the event log.

Nothing here touches the network or the real ``~/.mifit``: log paths are under
``tmp_path`` and the autouse ``_isolated_home`` fixture points the home directory
there as well. Every credential and id below is synthetic.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mifit import paths
from mifit.auth.lifetime import describe_session, format_age, record_event, session_ages
from mifit.auth.session import Session

SYNTH_USER_ID = "1000001"
SYNTH_C_USER_ID = "synthetic-cuser-id"
SYNTH_SERVICE_TOKEN = "SYNTH-SERVICE-TOKEN-LIFE-0001"
SYNTH_SSECURITY = "c3ludGgtc3NlY3VyaXR5LWxpZmU="
SYNTH_PASS_TOKEN = "SYNTH-PASS-TOKEN-LIFE-0001"
SYNTH_DEVICE_ID = "SYNTHDEVICE-LIFE-01"
SYNTH_COOKIE = "SYNTH-EXTRA-COOKIE-LIFE-0001"
ALL_SECRETS = (
    SYNTH_USER_ID,
    SYNTH_C_USER_ID,
    SYNTH_SERVICE_TOKEN,
    SYNTH_SSECURITY,
    SYNTH_PASS_TOKEN,
    SYNTH_DEVICE_ID,
    SYNTH_COOKIE,
)

CREATED_AT = "2026-10-14T03:18:19Z"
RENEWED_AT = "2026-10-14T09:20:19Z"
NOW = datetime(2026, 10, 14, 10, 20, 19, tzinfo=timezone.utc)


def make_session(**overrides):
    fields = dict(
        user_id=SYNTH_USER_ID,
        c_user_id=SYNTH_C_USER_ID,
        service_token=SYNTH_SERVICE_TOKEN,
        ssecurity=SYNTH_SSECURITY,
        region="cn",
        created_at=CREATED_AT,
        extra_cookies={"synthetic": SYNTH_COOKIE},
        pass_token=SYNTH_PASS_TOKEN,
        device_id=SYNTH_DEVICE_ID,
    )
    fields.update(overrides)
    return Session(**fields)


# ---------------------------------------------------------------------------
# session_ages / format_age
# ---------------------------------------------------------------------------


def test_ages_from_login_and_renewal():
    never_renewed = make_session()
    renewed = make_session(renewed_at=RENEWED_AT)

    assert session_ages(never_renewed, NOW) == (25320, 25320)
    assert session_ages(renewed, NOW) == (25320, 3600)


def test_unparsable_time_gives_unknown_age():
    garbled_login = make_session(created_at="not a time", renewed_at=RENEWED_AT)
    garbled_renewal = make_session(renewed_at="yesterday-ish")
    empty_login = make_session(created_at="")

    assert session_ages(garbled_login, NOW) == (None, 3600)
    assert session_ages(garbled_renewal, NOW) == (25320, None)
    assert session_ages(empty_login, NOW) == (None, None)


def test_a_time_in_the_future_is_clamped_to_zero():
    future = make_session(created_at="2026-10-15T00:00:00Z")

    assert session_ages(future, NOW) == (0, 0)


def test_ages_accept_an_explicit_utc_offset_and_default_now_to_the_clock():
    offset_form = make_session(created_at="2026-10-14T03:18:19+00:00")

    assert session_ages(offset_form, NOW) == (25320, 25320)
    login_age, token_age = session_ages(make_session(created_at="2000-01-01T00:00:00Z"))
    assert login_age is not None and login_age > 0
    assert token_age == login_age


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "unknown"),
        (0, "0m"),
        (2700, "45m"),
        (25320, "7h02m"),
        (183600, "2d03h"),
    ],
)
def test_format_age(seconds, expected):
    assert format_age(seconds) == expected


# ---------------------------------------------------------------------------
# describe_session
# ---------------------------------------------------------------------------


def test_describe_session_never_renewed():
    text = describe_session(make_session(), NOW)

    assert text == (
        "logged in 2026-10-14T03:18:19Z (7h02m ago); never renewed; "
        "automatic renewal available"
    )


def test_describe_session_renewed():
    text = describe_session(make_session(renewed_at=RENEWED_AT), NOW)

    assert text == (
        "logged in 2026-10-14T03:18:19Z (7h02m ago); "
        "last renewed 2026-10-14T09:20:19Z (1h00m ago); "
        "automatic renewal available"
    )


def test_describe_session_without_pass_token():
    text = describe_session(make_session(pass_token="", device_id=""), NOW)

    assert text == (
        "logged in 2026-10-14T03:18:19Z (7h02m ago); never renewed; "
        "automatic renewal unavailable (no passToken: run `mifit login` once to enable it)"
    )


def test_describe_session_with_unparsable_times_says_unknown():
    text = describe_session(make_session(created_at="garbled", renewed_at="also garbled"), NOW)

    assert "(unknown ago)" in text
    assert "last renewed also garbled (unknown ago)" in text


def test_describe_session_shows_no_credential():
    text = describe_session(make_session(renewed_at=RENEWED_AT), NOW)

    for secret in ALL_SECRETS:
        assert secret not in text


# ---------------------------------------------------------------------------
# record_event
# ---------------------------------------------------------------------------


def test_record_event_appends_one_json_line_without_secrets(tmp_path):
    log = tmp_path / "logs" / "session-log.jsonl"
    session = make_session(renewed_at=RENEWED_AT)

    record_event("unauthorized", session, now=NOW, path=log)
    record_event("renewed", session, now=NOW, path=log)

    text = log.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    for entry in entries:
        assert set(entry) == {"event", "at", "login_age_s", "token_age_s"}
    assert [entry["event"] for entry in entries] == ["unauthorized", "renewed"]
    assert entries[0] == {
        "event": "unauthorized",
        "at": "2026-10-14T10:20:19Z",
        "login_age_s": 25320,
        "token_age_s": 3600,
    }
    for secret in ALL_SECRETS:
        assert secret not in text


def test_record_event_keeps_an_unknown_age_as_null(tmp_path):
    log = tmp_path / "session-log.jsonl"

    record_event("login", make_session(created_at="garbled"), now=NOW, path=log)

    entry = json.loads(log.read_text(encoding="utf-8"))
    assert entry["login_age_s"] is None
    assert entry["token_age_s"] is None


def test_record_event_defaults_to_the_log_next_to_the_session():
    record_event("login", make_session(created_at="2026-10-14T00:00:00Z"))

    log = paths.default_session_log_path()
    assert log.is_file()
    entry = json.loads(log.read_text(encoding="utf-8"))
    assert entry["event"] == "login"
    assert entry["at"].endswith("Z")


def test_record_event_never_raises(tmp_path):
    a_directory = tmp_path / "is-a-directory"
    a_directory.mkdir()

    record_event("login", make_session(), now=NOW, path=a_directory)  # no exception

    blocked_parent = tmp_path / "a-file"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    record_event("login", make_session(), now=NOW, path=blocked_parent / "session-log.jsonl")


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def test_default_session_log_path_is_next_to_the_session():
    log = paths.default_session_log_path()

    assert isinstance(log, Path)
    assert log.name == paths.SESSION_LOG_FILE_NAME == "session-log.jsonl"
    assert log.parent == paths.default_session_path().parent == paths.app_data_dir()


def test_tests_run_with_an_isolated_home(tmp_path):
    """Guards the conftest fixture: nothing may resolve to the real home directory."""
    assert paths.app_data_dir().is_relative_to(tmp_path)
    assert Path.home().is_relative_to(tmp_path)
