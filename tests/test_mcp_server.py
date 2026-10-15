"""``mifit.mcp_server.server``: the read tools, over an in-process MCP connection to a
fake client. Nothing here touches the network or the real session; every credential
and figure is synthetic."""

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

import mifit
from mcp_fakes import (
    P1_TOOLS,
    PROFILE,
    RAW_MARKER,
    SECRETS,
    WORKOUT,
    FakeClient,
    call_tool,
    call_tools_at_once,
    list_tools,
    payload,
    server_info,
    text_of,
)
from mifit.chunks import plan_chunks
from mifit.errors import ApiError, AuthError, RenewalError
from mifit.mcp_server import errors, tools
from mifit.mcp_server import server as server_module
from mifit.mcp_server.present import present_record, without_stages
from mifit.mcp_server.server import build_server
from mifit.models.workout_detail import WorkoutDetail

pytestmark = pytest.mark.usefixtures("allow_loopback")

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 15, 9, 30, tzinfo=CST)
DAILY_AND_SLEEP = [t for t in tools.DATA_TOOLS if t.kind in ("daily", "sleep")]
WEEK_START = 1791302400  # 2026-10-07 00:00 +08:00
WEEK = [(WEEK_START + 60 * i, 60 + i % 20) for i in range(7 * 1440)]
ONE_DAY = {"start_date": "2026-10-01"}
DETAIL = {"workout_start": "2026-10-01T10:00:00+08:00"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class CountingFactory:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def build(fake, *, allow_gps=False, factory=None, clock=None, loader=None):
    return build_server(
        factory if factory is not None else (lambda: fake),
        allow_gps=allow_gps,
        tz=CST,
        now=lambda: NOW,
        clock=clock if clock is not None else Clock(),
        session_loader=loader if loader is not None else (lambda: fake.session),
    )


def args_for(name):
    tool = {t.name: t for t in tools.DATA_TOOLS}.get(name)
    if tool is None:
        return {}
    return DETAIL if tool.kind == "detail" else ONE_DAY


@pytest.fixture
def fake():
    return FakeClient()


def test_identity_and_instructions(fake):
    server = build(fake)
    assert server.name == "mifit"
    assert server.instructions == server_module.INSTRUCTIONS
    info = server_info(server)
    assert (info.name, info.version) == ("mifit", mifit.__version__)


def test_instructions_text():
    lines = server_module.INSTRUCTIONS.splitlines()
    assert lines[0].startswith("mifit reads the user's own Mi Fitness (Xiaomi) health data")
    assert lines[1].startswith("Call get_status first")
    assert "92 days for daily tools (31 for get_calories)" in server_module.INSTRUCTIONS
    assert server_module.INSTRUCTIONS.endswith("This is not medical advice.")


def test_tools_list(fake):
    listed = {t.name: t for t in list_tools(build(fake))}
    assert set(listed) == P1_TOOLS
    for tool in listed.values():
        assert tool.description and tool.title and tool.output_schema is None
        a = tool.annotations
        assert (a.read_only_hint, a.idempotent_hint, a.open_world_hint) == (True, True, True)
    assert listed["get_status"].description == tools.STATUS_DESCRIPTION
    assert listed["get_sleep"].description == {t.name: t for t in tools.DATA_TOOLS}[
        "get_sleep"
    ].description


def test_input_schemas(fake):
    s = {t.name: t.input_schema for t in list_tools(build(fake))}
    assert s["get_steps"]["required"] == ["start_date"]
    assert set(s["get_steps"]["properties"]) == {"start_date", "end_date"}
    assert s["get_steps"]["properties"]["start_date"]["description"] == tools.START_DATE_DOC
    assert s["get_sleep"]["properties"]["include_stages"]["default"] is False
    assert set(s["get_heart_rate"]["properties"]) == {"start_date", "end_date", "interval_min"}
    assert s["get_workout_detail"]["required"] == ["workout_start"]
    assert set(s["get_workout_detail"]["properties"]) == {
        "workout_start", "interval_s", "include_track",
    }
    assert s["get_workout_detail"]["properties"]["include_track"]["default"] is False
    assert s["get_status"]["properties"] == {} and s["get_profile"]["properties"] == {}


@pytest.mark.parametrize("tool", DAILY_AND_SLEEP, ids=lambda t: t.name)
def test_daily_tools(fake, tool):
    data = payload(call_tool(build(fake), tool.name, ONE_DAY))
    assert list(data) == ["tool", "from", "to", "timezone", "records", "notes"]
    assert (data["tool"], data["from"], data["to"], data["timezone"]) == (
        tool.name, "2026-10-01", "2026-10-01", "UTC+08:00",
    )
    expected = [present_record(r, tz=CST) for r in fake.canned(tool.method).records]
    if tool.kind == "sleep":
        expected = [without_stages(r) for r in expected]
    assert data["records"] == expected
    assert data["notes"] == []
    assert fake.calls == [(tool.method, "2026-10-01", "2026-10-01", {})]


def test_no_raw_in_any_output(fake):
    server = build(fake)
    for name in P1_TOOLS:
        result = call_tool(server, name, args_for(name))
        assert not result.is_error, (name, text_of(result))
        assert RAW_MARKER not in text_of(result), name


def test_sleep_stages_only_on_request(fake):
    server = build(fake)
    plain = payload(call_tool(server, "get_sleep", ONE_DAY))
    assert "stages" not in plain["records"][0]["sessions"][0]
    staged = payload(call_tool(server, "get_sleep", {**ONE_DAY, "include_stages": True}))
    assert staged["records"] == [
        present_record(r, tz=CST) for r in fake.canned("sleep").records
    ]
    too_long = call_tool(
        server, "get_sleep",
        {"start_date": "2026-10-01", "end_date": "2026-10-08", "include_stages": True},
    )
    assert too_long.is_error
    assert "get_sleep with include_stages=true reads at most 7 days per call" in text_of(too_long)
    assert len(fake.calls) == 2


def test_heart_rate_is_bucketed_automatically(fake):
    fake.series_points = WEEK
    data = payload(
        call_tool(build(fake), "get_heart_rate", {"start_date": "2026-10-07", "end_date": "2026-10-13"})
    )
    assert list(data) == [
        "tool", "from", "to", "timezone", "resolution", "columns", "rows", "notes",
    ]
    assert data["resolution"] == "30 min buckets" and len(data["rows"]) == 336
    assert data["columns"] == ["start", "min_bpm", "avg_bpm", "max_bpm", "count"]
    assert data["notes"][-1].startswith("Heart rate shown as 30 min buckets (10080 samples)")


def test_heart_rate_raw_when_small(fake):
    data = payload(call_tool(build(fake), "get_heart_rate", ONE_DAY))
    assert data["resolution"] == "raw samples" and data["columns"] == ["time", "bpm"]
    assert data["rows"] == [  # in time order, without the uninterpreted kind
        ["2026-10-01T08:00:00+08:00", 69],
        ["2026-10-01T08:01:00+08:00", 71],
    ]


def test_spo2_raw_when_small(fake):
    data = payload(call_tool(build(fake), "get_spo2", ONE_DAY))
    assert data["resolution"] == "raw samples" and data["columns"] == ["time", "spo2_pct"]
    assert data["rows"] == [["2026-10-01T08:01:00+08:00", 97]]


def test_series_interval_errors(fake):
    fake.series_points = WEEK
    server = build(fake)
    week = {"start_date": "2026-10-07", "end_date": "2026-10-13"}
    too_fine = call_tool(server, "get_spo2", {**week, "interval_min": 5})
    assert too_fine.is_error and "500-row limit" in text_of(too_fine)
    out_of_range = call_tool(server, "get_spo2", {**week, "interval_min": 2000})
    assert out_of_range.is_error
    hourly = payload(call_tool(server, "get_spo2", {**week, "interval_min": 60}))
    assert hourly["resolution"] == "60 min buckets"


def test_workout_detail_by_start_time(fake):
    data = payload(call_tool(build(fake), "get_workout_detail", DETAIL))
    assert fake.calls == [("workout_detail", 1790820000, 1790820000, {"track": False})]
    assert list(data) == [
        "tool", "workout_start", "timezone", "workout", "samples", "track", "notes",
    ]
    assert data["workout_start"] == "2026-10-01T10:00:00+08:00"
    assert data["timezone"] == "UTC+08:00"
    assert data["workout"] == present_record(WORKOUT, tz=CST)
    assert data["samples"]["resolution"] == "every second"
    assert data["samples"]["columns"][0] == "time" and len(data["samples"]["rows"]) == 3
    assert data["track"] is None


def test_workout_detail_in_utc(fake):
    utc_workout = WorkoutDetail(
        workout=WORKOUT.__class__(**{**WORKOUT.__dict__, "utc_offset_s": 0}),
        samples=None,
        track=None,
    )
    fake.detail_records = [utc_workout]
    data = payload(call_tool(build(fake), "get_workout_detail", {"workout_start": 1790820000}))
    assert data["timezone"] == "UTC+00:00"
    assert data["workout_start"] == "2026-10-01T02:00:00+00:00"
    assert data["samples"] is None and data["track"] is None


def test_workout_detail_without_a_workout(fake):
    fake.detail_records = []
    data = payload(call_tool(build(fake), "get_workout_detail", DETAIL))
    assert (data["workout"], data["samples"], data["track"]) == (None, None, None)
    assert data["timezone"] == "UTC+08:00"
    assert data["notes"] == [
        "No workout starts at 2026-10-01T10:00:00+08:00; get_workouts gives the start times."
    ]


def test_gps_gate():
    fake = FakeClient()
    off = build(fake)
    refused = call_tool(off, "get_workout_detail", {**DETAIL, "include_track": True})
    assert refused.is_error and errors.GPS_DISABLED in text_of(refused)
    assert fake.calls == []
    call_tool(off, "get_workout_detail", DETAIL)
    assert fake.calls[-1][3] == {"track": False}

    fake = FakeClient()
    on = build(fake, allow_gps=True)
    no_track = payload(call_tool(on, "get_workout_detail", DETAIL))
    assert fake.calls[-1][3] == {"track": False} and no_track["track"] is None
    tracked = payload(call_tool(on, "get_workout_detail", {**DETAIL, "include_track": True}))
    assert fake.calls[-1][3] == {"track": True}
    assert tracked["track"]["columns"] == ["time", "latitude_deg", "longitude_deg"]
    assert len(tracked["track"]["rows"]) == 3


def test_input_errors_read_nothing(fake):
    server = build(fake)
    for name, arguments in [
        ("get_steps", {"start_date": "yesterday"}),
        ("get_steps", {"start_date": "2026-07-01", "end_date": "2026-10-01"}),
        ("get_calories", {"start_date": "2026-09-01", "end_date": "2026-10-02"}),
        ("get_heart_rate", {"start_date": "2026-10-01", "end_date": "2026-10-08"}),
        ("get_workout_detail", {"workout_start": "2026-10-01"}),
    ]:
        result = call_tool(server, name, arguments)
        assert result.is_error, name
    assert fake.calls == []
    bad_date = call_tool(server, "get_steps", {"start_date": "yesterday"})
    assert text_of(bad_date) == (
        "Error executing tool get_steps: start_date must be a local calendar date in "
        "YYYY-MM-DD form, such as 2026-10-07 (got 'yesterday'); call get_status for "
        "today's date."
    )


def test_long_range_is_read_in_chunks_with_progress(fake):
    events = []
    data = payload(
        call_tool(
            build(fake), "get_steps", {"start_date": "2026-07-01", "end_date": "2026-09-30"},
            progress=events,
        )
    )
    plan = plan_chunks("steps", date(2026, 7, 1), date(2026, 9, 30))
    assert [call[1:3] for call in fake.calls] == [
        (c.first.isoformat(), c.last.isoformat()) for c in plan
    ]
    assert [(value, total) for value, total, _ in events] == [(1, 3), (2, 3), (3, 3)]
    assert events[0][2] == f"read {plan[0].label}"
    assert len(data["records"]) == 3  # one canned record per chunk


def test_one_chunk_reports_no_progress(fake):
    events = []
    call_tool(build(fake), "get_steps", ONE_DAY, progress=events)
    assert events == []


def test_not_logged_in(fake):
    server = build(fake, factory=CountingFactory(AuthError("No valid Mi Fitness login session.")))
    result = call_tool(server, "get_steps", ONE_DAY)
    assert result.is_error
    assert text_of(result) == f"Error executing tool get_steps: {errors.NOT_LOGGED_IN}"


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (RenewalError("Xiaomi refused the passToken (code 70016)."), "could not be renewed"),
        (ApiError("HTTP 500 from the service."), "HTTP 500 from the service."),
        (
            requests.ConnectionError(f"https://api.example/?t={SECRETS[0]}"),
            "Network request failed (ConnectionError).",
        ),
        (RuntimeError(f"boom {SECRETS[1]}"), "Internal error in mifit (RuntimeError)."),
    ],
    ids=["renewal", "api", "network", "unexpected"],
)
def test_read_errors(fake, caplog, exc, text):
    caplog.set_level(logging.DEBUG)
    fake.fail = {"steps": exc}
    result = call_tool(build(fake), "get_steps", ONE_DAY)
    assert result.is_error and text in text_of(result)
    for secret in SECRETS:
        assert secret not in text_of(result) and secret not in caplog.text


def test_no_credential_in_any_output(caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    fake = FakeClient()
    server = build(fake, allow_gps=True)
    seen.append(json.dumps([t.model_dump(mode="json") for t in list_tools(server)]))
    for name in P1_TOOLS:
        seen.append(text_of(call_tool(server, name, args_for(name))))
    for exc in (
        RenewalError("refused"),
        ApiError("bad"),
        requests.ConnectionError(f"https://api.example/?t={SECRETS[0]}&s={SECRETS[2]}"),
        RuntimeError(" ".join(SECRETS)),
    ):
        failing = FakeClient()
        failing.fail = {"profile": exc}
        seen.append(text_of(call_tool(build(failing), "get_profile")))
    seen.append(caplog.text)
    everything = "\n".join(seen)
    for secret in SECRETS:
        assert secret not in everything, secret


def test_cache(fake):
    clock = Clock()
    server = build(fake, clock=clock)
    first = call_tool(server, "get_steps", ONE_DAY)
    second = call_tool(server, "get_steps", ONE_DAY)
    assert len(fake.calls) == 1 and text_of(first) == text_of(second)
    call_tool(server, "get_steps", {"start_date": "2026-10-02"})
    assert len(fake.calls) == 2  # other arguments: another read
    clock.now += 301
    call_tool(server, "get_steps", ONE_DAY)
    assert len(fake.calls) == 3


def test_errors_are_not_cached(fake):
    server = build(fake)
    fake.fail = {"steps": ApiError("bad")}
    assert call_tool(server, "get_steps", ONE_DAY).is_error
    fake.fail = {}
    assert not call_tool(server, "get_steps", ONE_DAY).is_error
    assert len(fake.calls) == 2


def test_reads_are_serial(fake):
    fake.delay_s = 0.05
    results = call_tools_at_once(
        build(fake),
        [("get_steps", ONE_DAY), ("get_weight", ONE_DAY), ("get_spo2_daily", ONE_DAY)],
    )
    assert not any(result.is_error for result in results)
    assert len(fake.calls) == 3 and fake.most_active == 1


def test_get_status_logged_in(fake):
    factory = CountingFactory(fake)
    data = payload(call_tool(build(fake, factory=factory), "get_status"))
    assert data == {
        "tool": "get_status",
        "version": mifit.__version__,
        "today": "2026-10-15",
        "timezone": "UTC+08:00",
        "logged_in": True,
        "session": {"login_age": "1d00h", "token_age": "1d00h", "auto_renew": True},
        "gps_enabled": False,
        "privacy": server_module.PRIVACY_NOTE,
    }
    assert factory.calls == 0  # status never opens a client


def test_get_status_reports_gps_and_status_is_not_cached(fake):
    clock = Clock()
    server = build(fake, allow_gps=True, clock=clock)
    assert payload(call_tool(server, "get_status"))["gps_enabled"] is True
    fake.session = None
    assert payload(call_tool(server, "get_status"))["logged_in"] is False


def test_get_status_without_a_session(fake):
    data = payload(call_tool(build(fake, loader=lambda: None), "get_status"))
    assert data["logged_in"] is False and data["session"] is None
    assert data["next_step"] == errors.NOT_LOGGED_IN


def test_get_status_with_an_unreadable_session(fake):
    def unreadable():
        raise ValueError("session file is not valid JSON")

    data = payload(call_tool(build(fake, loader=unreadable), "get_status"))
    assert data["logged_in"] is False and data["session"] is None
    assert data["next_step"] == (
        "The stored Mi Fitness session is unreadable. " + errors.LOGIN_ADVICE
    )


def test_get_profile(fake):
    data = payload(call_tool(build(fake), "get_profile"))
    assert data == {"tool": "get_profile", "records": [present_record(PROFILE)], "notes": []}
    assert fake.calls == [("profile", None, None, {})]
