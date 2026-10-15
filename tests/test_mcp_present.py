"""``mifit.mcp_server.present``: B-layer records as the MCP server's JSON.

The only changes to a record are its times: a day record drops ``epoch_s``, a moment
becomes a local ISO 8601 time with its offset. Every value here is synthetic.
"""

from dataclasses import replace
from datetime import date, timedelta, timezone

from mifit import jsonsafe
from mifit.mcp_server import present
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.steps import StepRecord
from mifit.models.weight import WeightRecord
from mifit.models.workouts import Workout

CST = timezone(timedelta(hours=8))

WORKOUT = Workout(
    start_epoch_s=1790820000,
    end_epoch_s=1790822701,
    utc_offset_s=28800,
    sport="running",
    source_sport_code=1,
    duration_s=2400,
    distance_m=5000,
    active_kcal=350,
    total_kcal=410,
    avg_hr_bpm=148,
    max_hr_bpm=170,
    steps=6000,
)
SESSION = SleepSession(
    start_epoch_s=1790812800,
    end_epoch_s=1790820000,
    utc_offset_s=28800,
    asleep_min=120,
    deep_min=60,
    light_min=60,
    rem_min=None,
    awake_min=0,
    stages=[SleepStage(1790812800, 1790816400, "deep")],
)
SLEEP_DAY = SleepRecord(
    date="2026-10-01",
    epoch_s=1790784000,
    asleep_min=120,
    deep_min=60,
    light_min=60,
    rem_min=None,
    awake_min=0,
    avg_hr_bpm=60,
    min_hr_bpm=55,
    max_hr_bpm=70,
    avg_spo2_pct=None,
    min_spo2_pct=None,
    max_spo2_pct=None,
    sessions=[SESSION],
)


def test_offset_label():
    assert [present.offset_label(s) for s in (28800, -12600, 0, 19800)] == [
        "UTC+08:00",
        "UTC-03:30",
        "UTC+00:00",
        "UTC+05:30",
    ]


def test_local_offset_s_reads_the_zone_at_that_moment():
    assert present.local_offset_s(1790820000, CST) == 28800
    assert present.local_offset_s(1790820000, timezone.utc) == 0


def test_iso_time_uses_the_given_offset_before_the_zone():
    assert present.iso_time(1791385920, tz=CST) == "2026-10-07T23:12:00+08:00"
    assert present.iso_time(1790820000, offset_s=0, tz=CST) == "2026-10-01T02:00:00+00:00"


def test_day_record_drops_epoch_s():
    rec = StepRecord(
        date="2026-10-01", steps=4321, distance_m=None, step_calories=12, epoch_s=1790784000
    )
    assert present.present_record(rec, tz=CST) == {
        "date": "2026-10-01",
        "steps": 4321,
        "distance_m": None,
        "step_calories": 12,
    }


def test_moment_record_gets_local_time():
    rec = WeightRecord(epoch_s=1790812800, weight_kg=70.5, bmi=None)
    assert present.present_record(rec, tz=CST) == {
        "time": "2026-10-01T08:00:00+08:00",
        "weight_kg": 70.5,
        "bmi": None,
    }


def test_workout_uses_its_own_offset_and_keeps_key_order():
    out = present.present_record(replace(WORKOUT, utc_offset_s=0, end_epoch_s=None), tz=CST)
    assert out["start"] == "2026-10-01T02:00:00+00:00" and out["end"] is None
    assert list(out)[:3] == ["start", "end", "utc_offset_s"]
    assert "start_epoch_s" not in out and "end_epoch_s" not in out
    assert out["utc_offset_s"] == 0 and out["sport"] == "running"


def test_sleep_sessions_and_stages_use_the_session_offset():
    out = present.present_record(SLEEP_DAY, tz=timezone.utc)
    assert "epoch_s" not in out and out["date"] == "2026-10-01"
    session = out["sessions"][0]
    assert session["start"] == "2026-10-01T08:00:00+08:00"
    assert session["end"] == "2026-10-01T10:00:00+08:00"
    assert session["stages"] == [
        {
            "start": "2026-10-01T08:00:00+08:00",
            "end": "2026-10-01T09:00:00+08:00",
            "stage": "deep",
        }
    ]


def test_stage_without_any_offset_uses_the_zone():
    day = replace(SLEEP_DAY, sessions=[replace(SESSION, utc_offset_s=None)])
    out = present.present_record(day, tz=timezone.utc)
    assert out["sessions"][0]["stages"][0]["start"] == "2026-10-01T00:00:00+00:00"
    assert out["sessions"][0]["utc_offset_s"] is None


def test_without_stages_drops_only_the_timelines():
    full = present.present_record(SLEEP_DAY, tz=CST)
    out = present.without_stages(full)
    assert "stages" not in out["sessions"][0]
    assert out["sessions"][0]["deep_min"] == SESSION.deep_min
    assert "stages" in full["sessions"][0]  # a copy: the input keeps its timelines


def test_range_header():
    assert present.range_header("get_steps", date(2026, 10, 1), date(2026, 10, 7), tz=CST) == {
        "tool": "get_steps",
        "from": "2026-10-01",
        "to": "2026-10-07",
        "timezone": "UTC+08:00",
    }


def test_encode_is_compact_and_replaces_nan_with_a_note():
    text = present.encode({"tool": "x", "records": [{"v": float("nan")}], "notes": []})
    assert text == (
        '{"tool":"x","records":[{"v":null}],"notes":["' + jsonsafe.nonfinite_note(1) + '"]}'
    )


def test_encode_leaves_finite_payloads_and_non_ascii_alone():
    text = present.encode({"tool": "x", "notes": ["活力指标"], "v": 1.5})
    assert text == '{"tool":"x","notes":["活力指标"],"v":1.5}'
