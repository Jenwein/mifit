"""MiFit facade tests. A fake ``Transport`` stands in for the network layer and the
session store is monkeypatched, so nothing here touches the network, the real
session file or any real health data. Every credential below is synthetic.
"""

import json
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

import pytest

import mifit
from mifit import client as client_module
from mifit.client import MiFit
from mifit.domains.base import REGISTRY, DomainResult
from mifit.domains.consumption import fetch_consumption
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.domains.sleep import fetch_sleep
from mifit.domains.steps import fetch_steps
from mifit.errors import AuthError
from mifit.auth.session import Session
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary, HeartRatePoint
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.steps import StepRecord
from mifit.transport import PagedResult, Transport

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


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the rows of a canned payload and records the call.

    Like the real ``get_paged`` it returns the inner entry list, not the
    ``{code, result}`` envelope.
    """

    def __init__(self, payload):
        # No Session/http needed: ``get_paged`` is fully replaced below.
        self._payload = payload
        self.calls = []
        self.json_calls = []

    def get_paged(
        self,
        endpoint_path,
        data_obj,
        *,
        list_key="data_list",
        sign_path=None,
        max_pages=None,
    ):
        self.calls.append(
            {
                "endpoint_path": endpoint_path,
                "data_obj": dict(data_obj),
                "list_key": list_key,
                "max_pages": max_pages,
            }
        )
        return list(self._payload["result"].get(list_key) or [])

    def get_paged_result(
        self,
        endpoint_path,
        data_obj,
        *,
        list_key="data_list",
        sign_path=None,
        max_pages=None,
    ):
        """What the full heart-rate read calls: the same rows, never truncated."""
        items = self.get_paged(
            endpoint_path,
            data_obj,
            list_key=list_key,
            sign_path=sign_path,
            max_pages=max_pages,
        )
        return PagedResult(items, False)

    def get_json(self, endpoint_path, data_obj, *, sign_path=None):
        """Serves the canned payload as the single-object envelope ``get_json`` returns."""
        self.json_calls.append(
            {"endpoint_path": endpoint_path, "data_obj": dict(data_obj)}
        )
        return self._payload


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def build(monkeypatch):
    """Return ``build(payload) -> (MiFit, FakeTransport, transport_args)``.

    ``mifit.client.Transport`` is replaced by a factory that records how the
    facade constructed it and hands back the fake.
    """

    def _build(payload, session=None, http=None):
        fake = FakeTransport(payload)
        args = []

        def factory(*a, **kw):
            args.append((a, kw))
            return fake

        monkeypatch.setattr(client_module, "Transport", factory)
        client = MiFit(session or make_session(), http=http)
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
    assert sent["data_obj"]["start_time"] == 1759248000
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

    # get_paged already unwraps ``result``: the facade must hand back its list as is.
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


def test_raw_converts_date_strings(build):
    client, fake, _ = build(load_fixture("hr_raw_sample.json"))

    client.raw("steps", "2026-10-01", "2026-10-01")

    sent = fake.calls[0]["data_obj"]
    assert datetime.fromtimestamp(sent["start_time"]) == datetime(2026, 10, 1, 0, 0, 0)
    assert datetime.fromtimestamp(sent["end_time"]) == datetime(2026, 10, 1, 23, 59, 59)


def test_transport_is_built_from_the_session_and_injected_http(build):
    session = make_session()
    http = object()

    _, _, args = build(load_fixture("steps_daily_sample.json"), session=session, http=http)

    assert len(args) == 1
    (positional, keywords) = args[0]
    assert positional == (session,)
    assert keywords == {"http": http}


def test_no_session_raises_AuthError(monkeypatch):
    monkeypatch.setattr(client_module, "load_session", lambda path=None: None)

    with pytest.raises(AuthError):
        MiFit()


def test_stored_session_is_loaded_when_none_passed(monkeypatch):
    stored = make_session()
    fake = FakeTransport(load_fixture("steps_daily_sample.json"))
    seen = []
    monkeypatch.setattr(client_module, "load_session", lambda path=None: stored)
    monkeypatch.setattr(
        client_module, "Transport", lambda *a, **kw: seen.append((a, kw)) or fake
    )

    client = MiFit()

    assert seen == [((stored,), {"http": None})]
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
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


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

    def factory(session, *, http=None):
        fake = FakeTransport(load_fixture("steps_daily_sample.json"))
        fake.session = session
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

    def factory(session, *, http=None):
        fake = FakeTransport(load_fixture("steps_daily_sample.json"))
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
        "['consumption', 'heart_rate', 'heart_rate_daily', 'sleep', 'steps']"
    )


def test_consumption_is_registered_once_client_is_imported():
    assert REGISTRY["consumption"] is fetch_consumption


def test_sleep_is_registered_once_client_is_imported():
    assert REGISTRY["sleep"] is fetch_sleep


def test_heart_rate_daily_is_registered_once_client_is_imported():
    assert REGISTRY["heart_rate_daily"] is fetch_heart_rate_daily


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


def test_package_exports():
    from mifit.auth.session import Session as SessionClass
    from mifit.errors import ApiError, MiFitError, NotificationRequiredError

    assert mifit.MiFit is MiFit
    assert mifit.Session is SessionClass
    assert mifit.AuthError is AuthError
    assert mifit.ApiError is ApiError
    assert mifit.MiFitError is MiFitError
    assert mifit.NotificationRequiredError is NotificationRequiredError
    assert mifit.StepRecord is StepRecord
    assert mifit.HeartRatePoint is HeartRatePoint
    assert mifit.HeartRateDailySummary is HeartRateDailySummary
    assert mifit.SleepRecord is SleepRecord
    assert mifit.SleepSession is SleepSession
    assert mifit.SleepStage is SleepStage
    assert mifit.ConsumptionRecord is ConsumptionRecord
    for name in (
        "MiFit",
        "Session",
        "MiFitError",
        "AuthError",
        "ApiError",
        "NotificationRequiredError",
        "StepRecord",
        "HeartRatePoint",
        "ConsumptionRecord",
    ):
        assert name in mifit.__all__
