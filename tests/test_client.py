"""MiFit facade tests. ``PagedFake`` (or, for the renewal tests, a fake
``http`` under the real one) stands in for the network, and the session store is
monkeypatched or lives under the isolated home (conftest), so nothing here
touches the network, the real session file or any real health data. Every
credential below is synthetic.
"""

import base64
import dataclasses
import json
import subprocess
import sys
import warnings
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
import requests

import mifit
from mifit import client as client_module
from mifit import paths
from mifit import transport as transport_module
from mifit.auth.renew import RENEW_URL
from mifit.auth.session import Session, load_session, save_session
from mifit.client import MiFit
from mifit.crypto import rc4_crypt, signed_nonce
from mifit.domains.base import REGISTRY, DomainResult
from mifit.domains.consumption import fetch_consumption
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.domains.sleep import fetch_sleep
from mifit.domains.spo2 import fetch_spo2_window
from mifit.domains.spo2_daily import fetch_spo2_daily
from mifit.domains.steps import fetch_steps
from mifit.domains.stress_daily import fetch_stress_daily
from mifit.domains.weight import fetch_weight
from mifit.domains.workout_detail import fetch_workout_detail
from mifit.domains.workouts import fetch_workouts
from mifit.errors import AuthError, RenewalError
from mifit.fds.download import FdsDownloader, _FreshSessionHttp
from mifit.region import SID
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary, HeartRatePoint
from mifit.models.profile import UserProfile
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.spo2 import Spo2DailySummary, Spo2Point
from mifit.models.steps import StepRecord
from mifit.models.stress import StressDailySummary
from mifit.models.weight import WeightRecord
from mifit.models.workout_detail import TrackPoint, WorkoutDetail, WorkoutSample
from mifit.models.workouts import Workout
from support import PagedFake, exception_chain

FIXTURES = Path(__file__).parent / "fixtures"
SRC = Path(__file__).resolve().parents[1] / "src"

STEPS_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
FITNESS_ENDPOINT = "data/get_fitness_data_by_time"
CONSUMPTION_ENDPOINT = "diet/user_consumption"


def make_session(token="SYNTH-SERVICE-TOKEN-0000", user_id="1000001"):
    return Session(
        user_id=user_id,
        c_user_id="synthetic-cuser-id",
        service_token=token,
        ssecurity="c3ludGhldGljLXNzZWN1cml0eQ==",
        region="us",
        created_at="2026-10-14T00:00:00+00:00",
    )


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def build(monkeypatch):
    """Return ``build(payload) -> (MiFit, PagedFake, transport_args)``.

    ``mifit.client.Transport`` is replaced by a factory that records how the
    facade constructed it and hands back the fake. ``stalled`` is passed to the
    fake; every other keyword goes to ``MiFit``.
    """

    def _build(payload, session=None, http=None, *, stalled=False, **kwargs):
        fake = PagedFake(payload, stalled=stalled)
        args = []

        def factory(*a, **kw):
            args.append((a, kw))
            return fake

        monkeypatch.setattr(client_module, "Transport", factory)
        client = MiFit(session or make_session(), http=http, **kwargs)
        return client, fake, args

    return _build


def test_steps_delegates_to_domain(build):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))

    result = client.steps(1760400000, 1760486399)

    # The fixture row is keyed 2025-10-14; ``epoch_s`` is that date's local midnight,
    # derived here so the test holds in any timezone.
    local_midnight = int(datetime(2025, 10, 14).timestamp())
    assert isinstance(result, DomainResult)
    assert result.records == [
        StepRecord(
            date="2025-10-14",
            steps=8000,
            distance_m=5200,
            step_calories=210,
            epoch_s=local_midnight,
        )
    ]
    # Same answer as calling the domain directly with the same transport.
    assert result == fetch_steps(fake, 1760400000, 1760486399)
    assert fake.calls[0]["endpoint_path"] == STEPS_ENDPOINT


CONSUMPTION_PAYLOAD = {
    "code": 0,
    "message": "success",
    "result": {"basal": 1907.5, "exercise": 365, "intake": 0},
}


def test_consumption_delegates_to_domain(build):
    client, fake, _ = build(CONSUMPTION_PAYLOAD)

    result = client.consumption(1760400000, 1760400000)

    # The day is the machine-local one holding that instant, requested by its local
    # midnight; derived here so the test holds in any timezone.
    local_day = datetime.fromtimestamp(1760400000).date()
    local_midnight = int(datetime.combine(local_day, time()).timestamp())
    assert isinstance(result, DomainResult)
    assert result.records == [
        ConsumptionRecord(
            date=local_day.isoformat(),
            epoch_s=local_midnight,
            resting_kcal=1907.5,
            active_kcal=365,
            intake_kcal=0,
            total_kcal=2272.5,
        )
    ]
    # One local day in the window: one per-day call, at that day's local midnight,
    # through the facade's own transport and not the paged one.
    assert fake.json_calls == [
        {"endpoint_path": CONSUMPTION_ENDPOINT, "data_obj": {"time": local_midnight}}
    ]
    assert fake.calls == []
    # Same answer as calling the domain directly with the same transport.
    assert result == fetch_consumption(fake, 1760400000, 1760400000)


def test_consumption_converts_date_strings_to_day_bounds(build, monkeypatch):
    client, fake, _ = build(CONSUMPTION_PAYLOAD)
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_consumption", spy)

    result = client.consumption("2026-10-01", "2026-10-03")

    assert result is sentinel
    (transport, start_s, end_s) = seen[0]
    assert transport is fake
    # Local day bounds, as for ``steps``: the end date's last second keeps it inclusive.
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


@pytest.fixture
def steps_spy(monkeypatch):
    """Replace the steps domain with a recorder of the bounds the facade passes."""
    seen = []

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((start_epoch_s, end_epoch_s))
        return DomainResult()

    monkeypatch.setattr(client_module, "fetch_steps", spy)
    return seen


def test_date_string_converts_to_epoch_day_bounds(build, steps_spy):
    client, _, _ = build(load_fixture("steps_daily_sample.json"))

    client.steps("2026-10-01", "2026-10-01")

    ((start_s, end_s),) = steps_spy
    # Reading the epoch seconds back as local time recovers the local day bounds,
    # whatever timezone the machine running the test is in.
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 1, 23, 59, 59)
    assert isinstance(start_s, int)
    assert isinstance(end_s, int)


def test_date_range_uses_start_of_first_day_and_end_of_last_day(build, steps_spy):
    client, _, _ = build(load_fixture("steps_daily_sample.json"))

    client.steps("2026-10-01", "2026-10-03")

    ((start_s, end_s),) = steps_spy
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_int_epoch_seconds_pass_through_unchanged(build, steps_spy):
    client, _, _ = build(load_fixture("steps_daily_sample.json"))

    client.steps(1759276800, 1759363199)

    assert steps_spy == [(1759276800, 1759363199)]


def test_to_epoch_s_reads_the_day_in_a_given_zone():
    utc = timezone.utc

    assert client_module.to_epoch_s("2026-10-01", tz=utc) == 1790812800
    assert client_module.to_epoch_s("2026-10-01", end_of_day=True, tz=utc) == 1790899199


@pytest.mark.parametrize(
    ("zone", "day", "midnight", "next_midnight"),
    [
        ("America/Santiago", "2026-04-04", 1775271600, 1775361600),
        ("Africa/Cairo", "2025-10-30", 1761771600, 1761861600),
        ("Asia/Beirut", "2025-10-25", 1761339600, 1761429600),
    ],
)
def test_end_of_day_is_the_second_before_the_next_local_midnight(
    zone, day, midnight, next_midnight
):
    # On these days the clocks go back an hour at midnight: 23:00 to 23:59:59 happens
    # twice, and the day's last second is the second 23:59:59, not the first.
    try:
        tz = ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        pytest.skip("IANA timezone database not available")

    assert next_midnight - midnight == 25 * 3600
    assert client_module.to_epoch_s(day, tz=tz) == midnight
    assert client_module.to_epoch_s(day, end_of_day=True, tz=tz) == next_midnight - 1


@pytest.mark.parametrize(
    "bad",
    ["2026/10/01", "2026-13-01", "2026-10-32", "20261001", "2026-10-1", "", "tomorrow", " 2026-10-01"],
)
def test_malformed_date_string_raises_value_error(build, bad):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))

    with pytest.raises(ValueError):
        client.steps(bad, "2026-10-01")
    assert fake.calls == []


@pytest.mark.parametrize("bad", [True, 1.5, None, b"2026-10-01"])
def test_non_date_non_int_argument_raises_type_error(build, bad):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))

    with pytest.raises(TypeError):
        client.steps(1759276800, bad)
    assert fake.calls == []


def test_heart_rate_window_delegates_to_domain(build):
    client, fake, _ = build(load_fixture("hr_raw_sample.json"))

    result = client.heart_rate(1759248000, 1759334399)

    assert result == fetch_heart_rate_window(fake, 1759248000, 1759334399)
    assert result.records and isinstance(result.records[0], HeartRatePoint)
    sent = fake.calls[0]
    assert sent["endpoint_path"] == FITNESS_ENDPOINT
    # The domain asks a day before the start and filters (notes/08).
    assert sent["data_obj"]["start_time"] == 1759248000 - 86400
    assert sent["data_obj"]["end_time"] == 1759334399


def test_heart_rate_full_pages_back_from_end_without_start(build):
    client, fake, _ = build(load_fixture("hr_raw_sample.json"))

    result = client.heart_rate("2026-10-01", "2026-10-02", full=True)

    expected_end = datetime(2026, 10, 2, 23, 59, 59).timestamp()
    sent = fake.calls[0]
    assert "start_time" not in sent["data_obj"]
    assert sent["data_obj"]["end_time"] == int(expected_end)
    assert result == fetch_heart_rate_full(fake, int(expected_end))


def test_raw_passes_entries_through_unwrapped(build):
    payload = load_fixture("hr_raw_sample.json")
    client, fake, _ = build(payload)

    entries = client.raw("heart_rate", 1759248000, 1759334399)

    # get_paged_result already unwraps ``result``: the facade must hand back its items as is.
    assert entries == payload["result"]["data_list"]
    assert isinstance(entries, list)
    assert fake.calls == [
        {
            "endpoint_path": FITNESS_ENDPOINT,
            "data_obj": {
                "key": "heart_rate",
                "start_time": 1759248000,
                "end_time": 1759334399,
                "reverse": True,
            },
            "list_key": "data_list",
            "max_pages": None,
        }
    ]


def test_raw_warns_when_the_read_stalls(build):
    payload = load_fixture("hr_raw_sample.json")
    client, _, _ = build(payload, stalled=True)

    with pytest.warns(RuntimeWarning, match="no usable cursor") as caught:
        entries = client.raw("heart_rate", 1759248000, 1759334399)

    # The rows that were read are still returned, and the warning names the key
    # and points at the caller.
    assert entries == payload["result"]["data_list"]
    (warning,) = caught
    assert "'heart_rate' rows" in str(warning.message)
    assert warning.filename == __file__


def test_raw_does_not_warn_on_a_complete_read(build):
    payload = load_fixture("hr_raw_sample.json")
    client, _, _ = build(payload)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        entries = client.raw("heart_rate", 1759248000, 1759334399)

    assert entries == payload["result"]["data_list"]


def test_raw_converts_date_strings(build):
    client, fake, _ = build(load_fixture("hr_raw_sample.json"))

    client.raw("steps", "2026-10-01", "2026-10-01")

    sent = fake.calls[0]["data_obj"]
    assert datetime.fromtimestamp(sent["start_time"]) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(sent["end_time"]) == datetime(2026, 10, 1, 23, 59, 59)


def test_raw_window_delegates_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("hr_raw_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, key, start_epoch_s, end_epoch_s):
        seen.append((transport, key, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_raw_rows", spy)

    result = client.raw_window("stress", "2026-10-01", "2026-10-02")

    assert result is sentinel
    ((transport, key, start_s, end_s),) = seen
    assert transport is fake
    assert key == "stress"
    # Local day bounds, as for ``steps``: the end date's last second keeps it inclusive.
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 2, 23, 59, 59)


def test_daily_report_delegates_with_tag(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, key, start_epoch_s, end_epoch_s, *, tag):
        seen.append((transport, key, start_epoch_s, end_epoch_s, tag))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_daily_rows", spy)

    assert client.daily_report("valid_stand", "2026-10-01", "2026-10-02") is sentinel
    assert client.daily_report(
        "goal", "2026-10-01", "2026-10-02", tag="daily_fitness"
    ) is sentinel

    (default, other) = seen
    assert default[0] is fake
    assert default[1] == "valid_stand"
    assert datetime.fromtimestamp(default[2]) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(default[3]) == datetime(2026, 10, 2, 23, 59, 59)
    assert default[4] == "daily_report"
    assert other[1] == "goal"
    assert other[2:4] == default[2:4]
    assert other[4] == "daily_fitness"


def test_transport_is_built_from_the_session_and_injected_http(build):
    session = make_session()
    http = object()

    client, _, args = build(
        load_fixture("steps_daily_sample.json"), session=session, http=http
    )

    assert len(args) == 1
    (positional, keywords) = args[0]
    assert positional == (session,)
    assert keywords == {"http": http, "renew": client._renew}


def test_transport_gets_the_renew_hook_unless_auto_renew_is_off(build):
    http = object()

    client, _, args = build(load_fixture("steps_daily_sample.json"), http=http)
    ((_, keywords),) = args
    assert keywords == {"http": http, "renew": client._renew}

    _, _, args = build(load_fixture("steps_daily_sample.json"), http=http, auto_renew=False)
    ((_, keywords),) = args
    assert keywords == {"http": http, "renew": None}


def test_session_property_returns_the_session_in_use(build, monkeypatch):
    first = make_session(token="SYNTH-FIRST-TOKEN-0001")
    second = make_session(token="SYNTH-SECOND-TOKEN-0002")
    client, _, _ = build(load_fixture("steps_daily_sample.json"), session=first)

    assert client.session is first
    with pytest.raises(AttributeError):
        client.session = second  # read-only: use login_qr to switch sessions

    monkeypatch.setattr(client_module, "_login_qr", lambda http=None, on_qr=None: second)
    monkeypatch.setattr(client_module, "save_session", lambda session, path=None: None)
    client.login_qr()

    assert client.session is second


def test_no_session_raises_AuthError(monkeypatch):
    monkeypatch.setattr(client_module, "load_session", lambda path=None: None)

    with pytest.raises(AuthError):
        MiFit()


def test_stored_session_is_loaded_when_none_passed(monkeypatch):
    stored = make_session()
    fake = PagedFake(load_fixture("steps_daily_sample.json"))
    seen = []
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    monkeypatch.setattr(
        client_module, "Transport", lambda *a, **kw: seen.append((a, kw)) or fake
    )

    client = MiFit()

    assert seen == [((stored,), {"http": None, "renew": client._renew})]
    assert client.steps(1759276800, 1759363199).records


def test_corrupt_stored_session_raises_AuthError(monkeypatch):
    secret = "SYNTH-CORRUPT-SECRET-9999"

    def corrupt(path=None):
        # What a corrupt/incomplete session.json makes the loader raise.
        raise ValueError(f"bad session file containing {secret}")

    monkeypatch.setattr(client_module, "load_session", corrupt)

    with pytest.raises(AuthError) as excinfo:
        MiFit()

    assert not isinstance(excinfo.value, ValueError)
    assert secret not in str(excinfo.value)
    assert exception_chain(excinfo.value) == [excinfo.value]


def test_corrupt_session_file_leaves_no_chain():
    """The real loader on a real (truncated) session file: the ``JSONDecodeError``
    keeps the whole document, token included, in ``.doc``, and nothing a caller can
    reach from the ``AuthError`` may hold it."""
    path = paths.default_session_path()  # under the isolated home (conftest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"service_token": "SYNTH-TOKEN-CORRUPT",', encoding="utf-8")

    with pytest.raises(AuthError) as excinfo:
        MiFit()

    assert exception_chain(excinfo.value) == [excinfo.value]
    assert "SYNTH-TOKEN-CORRUPT" not in str(excinfo.value) + repr(excinfo.value.args)


def test_invalid_stored_session_raises_AuthError(monkeypatch):
    monkeypatch.setattr(
        client_module, "load_session", lambda path=None: make_session(token="")
    )

    with pytest.raises(AuthError):
        MiFit()


def test_invalid_explicit_session_raises_AuthError(monkeypatch):
    # An explicit session is used as given; the store is not consulted.
    monkeypatch.setattr(
        client_module,
        "load_session",
        lambda path=None: pytest.fail("store must not be read when a session is passed"),
    )

    with pytest.raises(AuthError):
        MiFit(make_session(user_id=""))


def test_auth_error_message_does_not_leak_credentials():
    session = make_session(user_id="")
    with pytest.raises(AuthError) as excinfo:
        MiFit(session)

    assert session.service_token not in str(excinfo.value)
    assert session.ssecurity not in str(excinfo.value)


def test_login_qr_stores_session_and_switches_transport(monkeypatch):
    old_session = make_session(token="SYNTH-OLD-TOKEN")
    new_session = make_session(token="SYNTH-NEW-TOKEN", user_id="2000002")
    http = object()
    built = []
    saved = []
    login_calls = []

    def factory(session, *, http=None, renew=None):
        fake = PagedFake(load_fixture("steps_daily_sample.json"))
        fake._session = session  # read back through ``Transport.session``
        built.append(fake)
        return fake

    def fake_login(http=None, on_qr=None):
        login_calls.append({"http": http, "on_qr": on_qr})
        return new_session

    def on_qr(login_url, qr_payload):
        pass

    monkeypatch.setattr(client_module, "Transport", factory)
    monkeypatch.setattr(client_module, "_login_qr", fake_login)
    monkeypatch.setattr(
        client_module, "save_session", lambda session, path=None: saved.append(session)
    )
    client = MiFit(old_session, http=http)

    returned = client.login_qr(on_qr=on_qr)

    assert returned is None
    assert login_calls == [{"http": http, "on_qr": on_qr}]
    assert saved == [new_session]
    # A fresh transport was built from the new session and is the one in use.
    assert [t.session for t in built] == [old_session, new_session]
    client.steps(1759276800, 1759363199)
    assert built[0].calls == []
    assert len(built[1].calls) == 1


def test_login_qr_failure_keeps_the_existing_session(monkeypatch):
    old_session = make_session()
    built = []
    saved = []

    def factory(session, *, http=None, renew=None):
        fake = PagedFake(load_fixture("steps_daily_sample.json"))
        built.append(fake)
        return fake

    def failing_login(http=None, on_qr=None):
        raise AuthError("login refused")

    monkeypatch.setattr(client_module, "Transport", factory)
    monkeypatch.setattr(client_module, "_login_qr", failing_login)
    monkeypatch.setattr(
        client_module, "save_session", lambda session, path=None: saved.append(session)
    )
    client = MiFit(old_session)

    with pytest.raises(AuthError):
        client.login_qr()

    assert saved == []
    assert len(built) == 1
    assert client.steps(1759276800, 1759363199).records
    assert len(built[0].calls) == 1


# ---------------------------------------------------------------------------
# renewal on HTTP 401
# ---------------------------------------------------------------------------

SYNTH_PASS_TOKEN = "SYNTH-PASS-TOKEN-0000"
SYNTH_DEVICE_ID = "SYNTHDEVICE00001"
NEW_SERVICE_TOKEN = "SYNTH-RENEWED-SERVICE-TOKEN-1111"
NEW_PASS_TOKEN = "SYNTH-RENEWED-PASS-TOKEN-1111"
NEW_SSECURITY = base64.b64encode(b"synthetic-renewed-ssecurity").decode("ascii")
STS_LOCATION = "https://sts.synthetic.example.com/sts?d=synthetic-renew&ticket=0"
CLIENT_SIGN = "SYNTH-CLIENT-SIGN-0000"
ROWS = [{"time": 1759276800, "value": "{\"steps\": 1}"}]


def renewable_session(token="SYNTH-SERVICE-TOKEN-0000"):
    return dataclasses.replace(
        make_session(token=token), pass_token=SYNTH_PASS_TOKEN, device_id=SYNTH_DEVICE_ID
    )


class RenewalRecorder:
    """Patches ``renew_session``, ``save_session`` and ``record_event`` on the client
    module and records how the facade used them."""

    def __init__(self, monkeypatch, *, result=None, error=None, save_error=None):
        self.result = result
        self.error = error
        self.save_error = save_error
        self.renew_calls = []
        self.saved = []
        self.events = []
        monkeypatch.setattr(client_module, "renew_session", self._renew)
        monkeypatch.setattr(client_module, "save_session", self._save)
        monkeypatch.setattr(client_module, "record_event", self._record)

    def _renew(self, session, http=None):
        self.renew_calls.append({"session": session, "http": http})
        if self.error is not None:
            raise self.error
        return self.result

    def _save(self, session, path=None):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(session)

    def _record(self, event, session, **kwargs):
        self.events.append((event, session))

    @property
    def event_names(self):
        return [event for event, _ in self.events]


def test_renew_hook_persists_and_logs_for_a_stored_session(monkeypatch):
    stored = renewable_session()
    new = renewable_session(token=NEW_SERVICE_TOKEN)
    http = object()
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    recorder = RenewalRecorder(monkeypatch, result=new)
    client = MiFit(http=http)

    returned = client._renew(stored)

    assert returned is new
    assert recorder.renew_calls == [{"session": stored, "http": http}]
    assert recorder.saved == [new]
    assert recorder.event_names == ["unauthorized", "renewed"]
    # The 401 is logged with the refused session's ages, the renewal with the new one's.
    assert recorder.events[0][1] is stored
    assert recorder.events[1][1] is new
    assert client.session is new


def test_renew_hook_does_not_write_for_an_explicit_session(monkeypatch):
    explicit = renewable_session()
    new = renewable_session(token=NEW_SERVICE_TOKEN)
    recorder = RenewalRecorder(monkeypatch, result=new)
    client = MiFit(explicit)

    returned = client._renew(explicit)

    assert returned is new
    assert recorder.saved == []
    assert recorder.events == []
    # Renewed in memory: the caller keeps it by reading ``client.session``.
    assert client.session is new


def test_failed_renewal_is_logged_and_re_raised(monkeypatch):
    stored = renewable_session()
    error = RenewalError("Xiaomi refused the stored passToken (code 70016).", code=70016)
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    recorder = RenewalRecorder(monkeypatch, error=error)
    client = MiFit()

    with pytest.raises(RenewalError) as excinfo:
        client._renew(stored)

    assert excinfo.value is error
    assert recorder.event_names == ["unauthorized", "renew_failed"]
    assert recorder.saved == []
    assert client.session is stored


def test_session_that_cannot_renew_logs_renew_unavailable(monkeypatch):
    # No passToken: ``renew_session`` refuses before any request, so the account
    # service gave no verdict and the log must not say it did (``renew_failed``).
    stored = make_session()
    assert not stored.can_renew()
    error = RenewalError("This session has no passToken or user id to renew with.")
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    recorder = RenewalRecorder(monkeypatch, error=error)
    client = MiFit()

    with pytest.raises(RenewalError) as excinfo:
        client._renew(stored)

    assert excinfo.value is error
    assert recorder.event_names == ["unauthorized", "renew_unavailable"]
    assert recorder.saved == []
    assert client.session is stored


def assert_chain_hides_the_sts_url(exc, *secrets):
    """No link of ``exc``'s chain carries the STS host or any of ``secrets``, in its
    message, its args or its attributes (a ``JSONDecodeError`` keeps the whole
    document in ``.doc``)."""
    for link in exception_chain(exc):
        text = str(link) + repr(link) + repr(link.args) + repr(vars(link))
        for secret in ("sts.synthetic.example.com", *secrets):
            assert secret not in text, f"{type(link).__name__} carries {secret!r}"
        # A requests exception keeps the request it was sending, URL and all.
        assert getattr(link, "request", None) is None


def sts_connection_error(url):
    """What requests raises when the STS call cannot connect: the URL, with its
    one-time ticket and clientSign, is in the message, in ``.request`` and in the
    urllib3 error it was raised from."""
    try:
        raise OSError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
    except OSError as inner:
        try:
            raise requests.ConnectionError(
                f"Max retries exceeded with url: {url}", request=SimpleNamespace(url=url)
            ) from inner
        except requests.ConnectionError as error:
            return error


def test_network_failure_during_renewal_stays_a_network_error_without_the_url(monkeypatch):
    stored = renewable_session()
    error = sts_connection_error(f"{STS_LOCATION}&clientSign={CLIENT_SIGN}")
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    recorder = RenewalRecorder(monkeypatch, error=error)
    client = MiFit()

    with pytest.raises(requests.ConnectionError) as excinfo:
        client._renew(stored)

    exc = excinfo.value
    # The same type, so callers and the CLI treat it as the network failure it
    # is, but a fresh object: nothing of the original is reachable from it.
    assert type(exc) is requests.ConnectionError
    assert exc is not error
    assert "renewal" in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None
    assert_chain_hides_the_sts_url(exc, CLIENT_SIGN)
    assert recorder.event_names == ["unauthorized", "renew_network_error"]
    assert client.session is stored


def test_network_failure_of_an_unbuildable_type_becomes_a_requests_connection_error(monkeypatch):
    class PickyNetworkError(OSError):
        """An OSError that cannot be built from a message alone."""

        def __init__(self, url, attempts):
            super().__init__(f"{attempts} attempts at {url}")

    explicit = renewable_session()
    error = PickyNetworkError(f"{STS_LOCATION}&clientSign={CLIENT_SIGN}", 3)
    RenewalRecorder(monkeypatch, error=error)
    client = MiFit(explicit)

    with pytest.raises(requests.ConnectionError) as excinfo:
        client._renew(explicit)

    assert type(excinfo.value) is requests.ConnectionError
    assert excinfo.value.__context__ is None
    assert_chain_hides_the_sts_url(excinfo.value, CLIENT_SIGN)


def test_save_failure_after_renewal_warns_and_keeps_going(monkeypatch):
    stored = renewable_session()
    new = dataclasses.replace(
        renewable_session(token=NEW_SERVICE_TOKEN), pass_token=NEW_PASS_TOKEN
    )
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    recorder = RenewalRecorder(
        monkeypatch, result=new, save_error=PermissionError(13, "synthetic: access denied")
    )
    client = MiFit()

    with pytest.warns(RuntimeWarning, match="could not be saved") as caught:
        returned = client._renew(stored)

    assert returned is new
    assert client.session is new
    assert recorder.event_names == ["unauthorized", "renewed"]
    (warning,) = caught
    message = str(warning.message)
    assert "next run will renew again" in message
    for secret in (NEW_SERVICE_TOKEN, NEW_PASS_TOKEN, NEW_SSECURITY, SYNTH_DEVICE_ID):
        assert secret not in message


def test_login_qr_keeps_the_renew_hook_and_becomes_store_backed(monkeypatch):
    explicit = make_session(token="SYNTH-EXPLICIT-TOKEN-0000")
    logged_in = renewable_session(token="SYNTH-LOGGED-IN-TOKEN-0000")
    renewed = renewable_session(token=NEW_SERVICE_TOKEN)
    built = []

    def factory(session, *, http=None, renew=None):
        built.append({"session": session, "renew": renew})
        return PagedFake(load_fixture("steps_daily_sample.json"))

    monkeypatch.setattr(client_module, "Transport", factory)
    monkeypatch.setattr(client_module, "_login_qr", lambda http=None, on_qr=None: logged_in)
    recorder = RenewalRecorder(monkeypatch, result=renewed)
    client = MiFit(explicit)

    client.login_qr()

    assert [entry["renew"] for entry in built] == [client._renew, client._renew]
    assert built[1]["session"] is logged_in
    assert recorder.saved == [logged_in]
    assert recorder.events == [("login", logged_in)]

    # The client logged in itself, so it is now store-backed: a renewal is saved and logged.
    client._renew(logged_in)

    assert recorder.saved == [logged_in, renewed]
    assert recorder.event_names == ["login", "unauthorized", "renewed"]


def encrypt_body(payload, nonce, ssecurity):
    """A health-API response body as the server builds it: b64(RC4(json))."""
    key = base64.b64decode(signed_nonce(ssecurity, nonce))
    return base64.b64encode(rc4_crypt(key, json.dumps(payload).encode("utf-8"))).decode(
        "ascii"
    )


class FakeHttpResponse:
    def __init__(self, status_code=200, text="", cookies=None):
        self.status_code = status_code
        self.text = text
        self.cookies = cookies or {}
        self.headers = {}


LOGIN_BODY = "&&&START&&&" + json.dumps(
    {
        "code": 0,
        "desc": "success",
        "userId": 1000001,
        "passToken": NEW_PASS_TOKEN,
        "cUserId": "synthetic-cuser-id",
        "ssecurity": NEW_SSECURITY,
        "nonce": 9876543210987654321,
        "location": STS_LOCATION,
        "securityStatus": 0,
    }
)


class RoutingHttp:
    """Answers by URL, like the real services would.

    The health API answers 401 and then 200 with ``ROWS`` encrypted under the
    renewed ssecurity (so only a retry signed for the new session decrypts);
    ``RENEW_URL`` answers the login JSON and the STS ``location`` sets the new
    service-token cookie.
    """

    def __init__(self, sts_failure=None, login_body=LOGIN_BODY):
        self.health_statuses = [401, 200]
        self.calls = []
        self.sts_failure = sts_failure  # None, "network" or an HTTP status
        self.login_body = login_body
        self.client_sign = None

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True, cookies=None):
        if url == RENEW_URL:
            self.calls.append("serviceLogin")
            return FakeHttpResponse(text=self.login_body)
        if url == STS_LOCATION:
            self.calls.append("sts")
            self.client_sign = params["clientSign"]
            if self.sts_failure == "network":
                raise sts_connection_error(f"{url}&clientSign={self.client_sign}")
            if self.sts_failure is not None:
                return FakeHttpResponse(status_code=self.sts_failure)
            return FakeHttpResponse(cookies={f"{SID}_serviceToken": NEW_SERVICE_TOKEN})
        if urlparse(url).hostname.endswith("hlth.io.mi.com"):
            self.calls.append("health")
            status = self.health_statuses.pop(0)
            if status != 200:
                return FakeHttpResponse(status_code=status)
            payload = {"code": 0, "result": {"data_list": ROWS, "has_more": False}}
            return FakeHttpResponse(text=encrypt_body(payload, params["_nonce"], NEW_SSECURITY))
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture
def no_delay(monkeypatch):
    """The facade's transport pauses before every request; not in these tests."""
    monkeypatch.setattr(transport_module.time, "sleep", lambda seconds: None)


def test_end_to_end_401_is_renewed_offline(no_delay):
    old = renewable_session()
    http = RoutingHttp()
    client = MiFit(old, http=http)

    rows = client.raw("steps", 1, 2)

    assert rows == ROWS
    assert client.session.service_token == NEW_SERVICE_TOKEN
    assert client.session.ssecurity == NEW_SSECURITY
    assert client.session.pass_token == NEW_PASS_TOKEN
    assert http.calls == ["health", "serviceLogin", "sts", "health"]


@pytest.mark.parametrize(
    ("sts_failure", "expected"),
    [("network", requests.ConnectionError), (500, RenewalError)],
)
def test_failed_renewal_never_chains_to_the_sts_url(no_delay, sts_failure, expected):
    """Real ``Transport`` and ``renew_session``: whatever a caller catches when the
    STS call fails, nothing reachable from it holds the STS URL or the clientSign."""
    old = renewable_session()
    http = RoutingHttp(sts_failure=sts_failure)
    client = MiFit(old, http=http)

    with pytest.raises(expected) as excinfo:
        client.raw("steps", 1, 2)

    assert type(excinfo.value) is expected
    assert http.calls == ["health", "serviceLogin", "sts"]
    assert http.client_sign
    assert exception_chain(excinfo.value) == [excinfo.value]
    assert_chain_hides_the_sts_url(
        excinfo.value, http.client_sign, NEW_PASS_TOKEN, NEW_SSECURITY, SYNTH_PASS_TOKEN
    )
    assert client.session is old


MALFORMED_LOGIN_BODY = (
    "&&&START&&&"
    + '{"code": 0, "passToken": "' + NEW_PASS_TOKEN + '", "ssecurity": "' + NEW_SSECURITY
    + '", "location": "' + STS_LOCATION + '", "MARKER": "SYNTH-LOGIN-BODY-MARKER", '
)


def test_malformed_service_login_body_never_chains_to_the_response(no_delay):
    """The serviceLogin answer is not valid JSON but holds the new passToken: the
    ``JSONDecodeError`` keeps all of it in ``.doc``, and it must not be reachable
    (``__cause__``/``__context__``) from the error a caller catches."""
    old = renewable_session()
    http = RoutingHttp(login_body=MALFORMED_LOGIN_BODY)
    client = MiFit(old, http=http)

    with pytest.raises(RenewalError) as excinfo:
        client.raw("steps", 1, 2)

    exc = excinfo.value
    assert http.calls == ["health", "serviceLogin"]
    assert "not valid JSON" in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None
    assert exception_chain(exc) == [exc]
    assert_chain_hides_the_sts_url(
        exc, NEW_PASS_TOKEN, NEW_SSECURITY, SYNTH_PASS_TOKEN, "SYNTH-LOGIN-BODY-MARKER"
    )
    assert client.session is old


def test_end_to_end_session_that_cannot_renew_logs_renew_unavailable(no_delay):
    stored = make_session()  # no passToken
    save_session(stored)  # under the isolated home (conftest)
    http = RoutingHttp()
    client = MiFit(http=http)

    with pytest.raises(RenewalError):
        client.raw("steps", 1, 2)

    assert http.calls == ["health"]  # nothing was sent to the account service
    log = paths.default_session_log_path().read_text(encoding="utf-8")
    assert [json.loads(line)["event"] for line in log.splitlines()] == [
        "unauthorized",
        "renew_unavailable",
    ]


def test_renewal_uses_its_own_http_client_and_saves_the_session(no_delay, monkeypatch):
    """With no injected ``http``, the renewal must not run on the transport's
    ``requests.Session``: the account and STS cookies stay out of the health
    API's cookie jar."""
    router = RoutingHttp()
    clients = []

    class RecordingSession:
        """Stands in for ``requests.Session``; every instance routes to ``router``."""

        def __init__(self):
            self.calls = []
            clients.append(self)

        def get(self, url, **kwargs):
            before = len(router.calls)
            response = router.get(url, **kwargs)
            self.calls.extend(router.calls[before:])
            return response

    monkeypatch.setattr(requests, "Session", RecordingSession)
    save_session(renewable_session())  # under the isolated home (conftest)
    client = MiFit()

    assert client.raw("steps", 1, 2) == ROWS

    transport_http, *renewal_clients = clients
    assert transport_http.calls == ["health", "health"]
    assert [call for c in renewal_clients for call in c.calls] == ["serviceLogin", "sts"]
    # Store-backed: the renewed session replaced the stored one, and the log
    # holds event names and ages only.
    assert load_session().service_token == NEW_SERVICE_TOKEN
    log = paths.default_session_log_path().read_text(encoding="utf-8")
    assert [json.loads(line)["event"] for line in log.splitlines()] == [
        "unauthorized",
        "renewed",
    ]
    for secret in (
        SYNTH_PASS_TOKEN,
        NEW_PASS_TOKEN,
        NEW_SERVICE_TOKEN,
        NEW_SSECURITY,
        SYNTH_DEVICE_ID,
        STS_LOCATION,
    ):
        assert secret not in log


def test_importing_client_registers_the_domains():
    # A fresh interpreter, so earlier imports in this test run cannot mask a
    # client module that forgot to import the domain modules.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import mifit.client;"
        "from mifit.domains.base import REGISTRY;"
        "print(sorted(REGISTRY))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code, str(SRC)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout.strip() == (
        "['consumption', 'heart_rate', 'heart_rate_daily', 'sleep', 'spo2', "
        "'spo2_daily', 'steps', 'stress_daily', 'weight', 'workout_detail', 'workouts']"
    )


def test_consumption_is_registered_once_client_is_imported():
    assert REGISTRY["consumption"] is fetch_consumption


def test_sleep_is_registered_once_client_is_imported():
    assert REGISTRY["sleep"] is fetch_sleep


def test_heart_rate_daily_is_registered_once_client_is_imported():
    assert REGISTRY["heart_rate_daily"] is fetch_heart_rate_daily


def test_workouts_is_registered_once_client_is_imported():
    assert REGISTRY["workouts"] is fetch_workouts


def test_spo2_daily_is_registered_once_client_is_imported():
    assert REGISTRY["spo2_daily"] is fetch_spo2_daily


def test_stress_daily_is_registered_once_client_is_imported():
    assert REGISTRY["stress_daily"] is fetch_stress_daily


def test_spo2_is_registered_once_client_is_imported():
    assert REGISTRY["spo2"] is fetch_spo2_window


def test_weight_is_registered_once_client_is_imported():
    assert REGISTRY["weight"] is fetch_weight


def test_workout_detail_is_registered_once_client_is_imported():
    assert REGISTRY["workout_detail"] is fetch_workout_detail


def test_sleep_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_sleep", spy)

    result = client.sleep("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_heart_rate_daily_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_heart_rate_daily", spy)

    result = client.heart_rate_daily("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_workouts_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_workouts", spy)

    result = client.workouts("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


@pytest.fixture
def workout_detail_spy(monkeypatch):
    """Replace the workout-detail domain with a recorder of what the facade passes."""
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s, *, downloader, track):
        seen.append(
            {
                "transport": transport,
                "start": start_epoch_s,
                "end": end_epoch_s,
                "downloader": downloader,
                "track": track,
            }
        )
        return sentinel

    monkeypatch.setattr(client_module, "fetch_workout_detail", spy)
    return seen, sentinel


def test_workout_detail_delegates_with_bounds_downloader_and_track(build, workout_detail_spy):
    seen, sentinel = workout_detail_spy
    store_http = object()
    client, fake, _ = build(
        load_fixture("steps_daily_sample.json"), http=object(), fds_http=store_http
    )

    result = client.workout_detail("2026-10-01", "2026-10-03", track=False)

    assert result is sentinel
    (call,) = seen
    assert call["transport"] is fake
    # Local day bounds, as for ``workouts``: the end date's last second keeps it inclusive.
    assert datetime.fromtimestamp(call["start"]) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(call["end"]) == datetime(2026, 10, 3, 23, 59, 59)
    # The files come through a downloader of their own, on the injected object-store
    # client, never on the health API's ``http``.
    assert isinstance(call["downloader"], FdsDownloader)
    assert call["downloader"]._http is store_http
    assert call["track"] is False

    client.workout_detail("2026-10-01", "2026-10-03")

    assert seen[1]["track"] is True


def test_workout_detail_by_epoch_reads_one_workout(build, workout_detail_spy):
    seen, _ = workout_detail_spy
    client, _, _ = build(load_fixture("steps_daily_sample.json"))

    client.workout_detail(1790820000, 1790820000)

    (call,) = seen
    assert call["start"] == call["end"] == 1790820000


def test_default_downloader_uses_a_fresh_netrc_free_session(build, workout_detail_spy):
    seen, _ = workout_detail_spy
    client, _, _ = build(load_fixture("steps_daily_sample.json"), http=object())

    client.workout_detail("2026-10-01", "2026-10-01")

    # Without ``fds_http`` the downloads open a new cookie-free, netrc-free
    # ``requests.Session`` each (Task 3's default), not the transport's ``http``.
    downloader = seen[0]["downloader"]
    assert isinstance(downloader._http, _FreshSessionHttp)


def test_read_chunks_forwards_options(build, monkeypatch):
    client, _, _ = build(load_fixture("steps_daily_sample.json"))
    captured = []
    monkeypatch.setattr(
        client_module.chunks,
        "read_chunks",
        lambda *args, **kwargs: captured.append((args, kwargs)) or iter(()),
    )
    options = {"track": False}

    client.read_chunks("workout_detail", "2026-10-01", "2026-10-14", options=options)
    client.read_chunks("steps", "2026-10-01", "2026-10-14")

    (args, kwargs), (_, default_kwargs) = captured
    assert args == (client, "workout_detail", "2026-10-01", "2026-10-14")
    assert kwargs["options"] == {"track": False}
    assert default_kwargs["options"] is None


def test_spo2_daily_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_spo2_daily", spy)

    result = client.spo2_daily("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_stress_daily_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_stress_daily", spy)

    result = client.stress_daily("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_spo2_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_spo2_window", spy)

    result = client.spo2("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_weight_delegates_to_domain_with_local_day_bounds(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport, start_epoch_s, end_epoch_s):
        seen.append((transport, start_epoch_s, end_epoch_s))
        return sentinel

    monkeypatch.setattr(client_module, "fetch_weight", spy)

    result = client.weight("2026-10-01", "2026-10-03")

    assert result is sentinel
    ((transport, start_s, end_s),) = seen
    assert transport is fake
    assert datetime.fromtimestamp(start_s) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(end_s) == datetime(2026, 10, 3, 23, 59, 59)


def test_profile_delegates_to_domain(build, monkeypatch):
    client, fake, _ = build(load_fixture("steps_daily_sample.json"))
    seen = []
    sentinel = DomainResult(notes=["sentinel"])

    def spy(transport):
        seen.append(transport)
        return sentinel

    monkeypatch.setattr(client_module, "fetch_profile", spy)

    result = client.profile()

    assert result is sentinel
    (transport,) = seen
    assert transport is fake


def test_package_exports():
    from mifit.auth.session import Session as SessionClass
    from mifit.errors import (
        ApiError,
        MiFitError,
        NotificationRequiredError,
        RenewalError,
    )

    assert mifit.MiFit is MiFit
    assert mifit.Session is SessionClass
    assert mifit.AuthError is AuthError
    assert mifit.ApiError is ApiError
    assert mifit.MiFitError is MiFitError
    assert mifit.NotificationRequiredError is NotificationRequiredError
    assert mifit.RenewalError is RenewalError
    assert mifit.StepRecord is StepRecord
    assert mifit.HeartRatePoint is HeartRatePoint
    assert mifit.HeartRateDailySummary is HeartRateDailySummary
    assert mifit.SleepRecord is SleepRecord
    assert mifit.SleepSession is SleepSession
    assert mifit.SleepStage is SleepStage
    assert mifit.ConsumptionRecord is ConsumptionRecord
    assert mifit.Workout is Workout
    assert mifit.Spo2DailySummary is Spo2DailySummary
    assert mifit.Spo2Point is Spo2Point
    assert mifit.StressDailySummary is StressDailySummary
    assert mifit.WeightRecord is WeightRecord
    assert mifit.UserProfile is UserProfile
    assert mifit.WorkoutDetail is WorkoutDetail
    assert mifit.WorkoutSample is WorkoutSample
    assert mifit.TrackPoint is TrackPoint
    for name in (
        "MiFit",
        "Session",
        "MiFitError",
        "AuthError",
        "ApiError",
        "NotificationRequiredError",
        "RenewalError",
        "StepRecord",
        "HeartRatePoint",
        "HeartRateDailySummary",
        "ConsumptionRecord",
        "SleepRecord",
        "SleepSession",
        "SleepStage",
        "Workout",
        "Spo2DailySummary",
        "Spo2Point",
        "StressDailySummary",
        "WeightRecord",
        "UserProfile",
        "WorkoutDetail",
        "WorkoutSample",
        "TrackPoint",
    ):
        assert name in mifit.__all__
