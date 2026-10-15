"""``mifit.mcp_server.tools``: the tool table, the input checks and the descriptions
the AI client reads to choose a tool."""

import re
from dataclasses import fields
from datetime import date

import pytest

from mifit.mcp_server import tools
from mifit.mcp_server.errors import ToolFailure
from mifit.models.activity import ActivityDailySummary
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary
from mifit.models.profile import UserProfile
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.spo2 import Spo2DailySummary
from mifit.models.steps import StepRecord
from mifit.models.stress import StressDailySummary
from mifit.models.weight import WeightRecord
from mifit.models.workout_detail import TrackPoint, WorkoutSample
from mifit.models.workouts import Workout

EPOCH = {"epoch_s", "start_epoch_s", "end_epoch_s"}


def _names(*models):
    """The output field names of these models: their fields after present_record's renames."""
    names = set()
    for model in models:
        own = {f.name for f in fields(model)}
        names |= own - EPOCH
        if "start_epoch_s" in own:
            names |= {"start", "end"}
        if "epoch_s" in own and "date" not in own:
            names.add("time")
    return names


FIELDS = {
    "get_steps": _names(StepRecord),
    "get_calories": _names(ConsumptionRecord),
    "get_activity": _names(ActivityDailySummary),
    "get_heart_rate_daily": _names(HeartRateDailySummary),
    "get_sleep": _names(SleepRecord, SleepSession, SleepStage),
    "get_spo2_daily": _names(Spo2DailySummary),
    "get_stress_daily": _names(StressDailySummary),
    "get_weight": _names(WeightRecord),
    "get_workouts": _names(Workout),
    "get_heart_rate": {"time", "bpm", "start", "min_bpm", "avg_bpm", "max_bpm", "count"},
    "get_spo2": {
        "time", "spo2_pct", "start", "min_spo2_pct", "avg_spo2_pct", "max_spo2_pct", "count",
    },
    "get_workout_detail": _names(Workout, WorkoutSample, TrackPoint)
    | {"workout", "samples", "track"},
}


def _words(text):
    return set(re.findall(r"[a-z_0-9]+", text))


def test_table_names_methods_and_limits():
    assert [(t.name, t.method, t.kind, t.max_days) for t in tools.DATA_TOOLS] == [
        ("get_steps", "steps", "daily", 92),
        ("get_calories", "consumption", "daily", 31),
        ("get_activity", "activity_daily", "daily", 92),
        ("get_heart_rate_daily", "heart_rate_daily", "daily", 92),
        ("get_sleep", "sleep", "sleep", 92),
        ("get_spo2_daily", "spo2_daily", "daily", 92),
        ("get_stress_daily", "stress_daily", "daily", 92),
        ("get_weight", "weight", "daily", 92),
        ("get_workouts", "workouts", "daily", 92),
        ("get_heart_rate", "heart_rate", "series", 7),
        ("get_spo2", "spo2", "series", 7),
        ("get_workout_detail", "workout_detail", "detail", 0),
    ]
    assert (tools.DAILY_MAX_DAYS, tools.CALORIES_MAX_DAYS) == (92, 31)
    assert (tools.SLEEP_STAGES_MAX_DAYS, tools.SERIES_MAX_DAYS) == (7, 7)
    assert all(t.title for t in tools.DATA_TOOLS)


@pytest.mark.parametrize("tool", tools.DATA_TOOLS, ids=lambda t: t.name)
def test_description_names_every_field_and_the_limit(tool):
    assert FIELDS[tool.name] <= _words(tool.description), FIELDS[tool.name] - _words(
        tool.description
    )
    if tool.max_days:
        assert f"{tool.max_days} days" in tool.description
    assert tool.description.endswith("Figures were checked against the Mi Fitness app.")


@pytest.mark.parametrize(
    "tool", [t for t in tools.DATA_TOOLS if t.kind in ("daily", "sleep")], ids=lambda t: t.name
)
def test_daily_descriptions_say_days_are_local_and_inclusive(tool):
    assert "local calendar days" in tool.description and "inclusive" in tool.description


def test_description_limitations():
    d = {t.name: t.description for t in tools.DATA_TOOLS}
    assert "calorie ring" in d["get_calories"] and "calorie ring" in d["get_steps"]
    assert "get_calories" in d["get_steps"]
    assert "outdoor" in d["get_workout_detail"] and "--allow-gps" in d["get_workout_detail"]
    assert "include_track" in d["get_workout_detail"] and "500" in d["get_workout_detail"]
    assert "500" in d["get_heart_rate"] and "interval_min" in d["get_heart_rate"]
    assert "500" in d["get_spo2"] and "interval_min" in d["get_spo2"]
    assert "include_stages" in d["get_sleep"] and "7 days" in d["get_sleep"]
    assert "get_workout_detail" in d["get_workouts"]
    assert "1-100" in d["get_stress_daily"]
    assert "活力指标" in d["get_activity"]
    assert "sparse" in d["get_weight"]


def test_profile_description_names_the_profile_fields():
    assert _names(UserProfile) <= _words(tools.PROFILE_DESCRIPTION)
    assert "personal details" in tools.PROFILE_DESCRIPTION.lower()


def test_status_description_says_what_it_gives():
    for text in ("today", "logged in", "time zone"):
        assert text in tools.STATUS_DESCRIPTION


def test_parameter_docs_exist():
    for doc in (
        tools.START_DATE_DOC, tools.END_DATE_DOC, tools.INCLUDE_STAGES_DOC,
        tools.INTERVAL_MIN_DOC, tools.WORKOUT_START_DOC, tools.INTERVAL_S_DOC,
        tools.INCLUDE_TRACK_DOC,
    ):
        assert doc.strip()
    assert "YYYY-MM-DD" in tools.START_DATE_DOC
    assert "--allow-gps" in tools.INCLUDE_TRACK_DOC


def test_parse_range_defaults_end_to_start():
    assert tools.parse_range("2026-10-07", None, max_days=92, tool="get_steps") == (
        date(2026, 10, 7),
        date(2026, 10, 7),
    )
    assert tools.parse_range("2026-10-01", "2026-10-07", max_days=7, tool="get_heart_rate") == (
        date(2026, 10, 1),
        date(2026, 10, 7),
    )


@pytest.mark.parametrize("value", ["yesterday", "2026/10/07", "2026-10-07T00:00", "2026-02-30", ""])
def test_parse_range_rejects_other_forms(value):
    with pytest.raises(ToolFailure) as err:
        tools.parse_range(value, None, max_days=92, tool="get_steps")
    assert str(err.value) == (
        "start_date must be a local calendar date in YYYY-MM-DD form, such as 2026-10-07 "
        f"(got {value!r}); call get_status for today's date."
    )


def test_parse_range_names_a_bad_end_date():
    with pytest.raises(ToolFailure, match="^end_date must be a local calendar date"):
        tools.parse_range("2026-10-07", "last week", max_days=92, tool="get_steps")


def test_parse_range_order_and_limit():
    with pytest.raises(ToolFailure, match="^end_date must not be before start_date.$"):
        tools.parse_range("2026-10-07", "2026-10-06", max_days=92, tool="get_steps")
    with pytest.raises(ToolFailure) as err:
        tools.parse_range("2026-07-01", "2026-10-01", max_days=92, tool="get_steps")
    assert str(err.value) == (
        "get_steps reads at most 92 days per call; 2026-07-01..2026-10-01 "
        "is 93 days. Split it into 2 calls of up to 92 days."
    )


def test_parse_range_takes_exactly_the_limit():
    assert tools.parse_range("2026-07-02", "2026-10-01", max_days=92, tool="get_steps")[0] == (
        date(2026, 7, 2)
    )


def test_workout_start_accepts_any_offset_and_unix_seconds():
    for value in (
        "2026-10-01T10:00:00+08:00",
        "2026-10-01T02:00:00Z",
        "2026-10-01T02:00:00+00:00",
        1790820000,
        "1790820000",
    ):
        assert tools.parse_workout_start(value) == 1790820000


@pytest.mark.parametrize(
    "value", [-5, "99999999999999999999999", "1900-01-01T00:00:00+00:00", 32503680000]
)
def test_workout_start_outside_any_workout_time_is_refused(value):
    with pytest.raises(ToolFailure) as err:
        tools.parse_workout_start(value)
    assert str(err.value).startswith("workout_start must be a workout's start")


@pytest.mark.parametrize("value", [1790820000000, "1790820000000"])
def test_workout_start_in_milliseconds_gets_a_hint(value):
    with pytest.raises(ToolFailure) as err:
        tools.parse_workout_start(value)
    assert str(err.value).endswith("(It looks like milliseconds: divide by 1000.)")


@pytest.mark.parametrize("value", ["2026-10-01T10:00:00", "yesterday", True, "", "2026-10-01"])
def test_workout_start_rejects_values_without_a_moment(value):
    with pytest.raises(ToolFailure) as err:
        tools.parse_workout_start(value)
    assert str(err.value) == (
        "workout_start must be a workout's start as get_workouts gives it, such as "
        "2026-10-01T10:00:00+08:00, or Unix seconds."
    )
