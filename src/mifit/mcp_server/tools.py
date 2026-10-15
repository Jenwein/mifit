"""The MCP server's tool table, its input checks and the descriptions the AI client
reads to choose a tool.

Each checked data domain is one tool, named for what it reads (``get_sleep``); the
server (``server.py``) builds them from ``DATA_TOOLS``, so every tool of a kind takes
the same parameters and answers the same way. The descriptions give every output
field with its meaning and unit, worded as the B-layer models word them, the limit
per call and the known limitations: they are all the AI client knows about the data.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from mifit.client import to_epoch_s
from mifit.domains.days import parse_day
from mifit.mcp_server.errors import ToolFailure

__all__ = [
    "CALORIES_MAX_DAYS",
    "DAILY_MAX_DAYS",
    "DATA_TOOLS",
    "DataTool",
    "END_DATE_DOC",
    "INCLUDE_STAGES_DOC",
    "INCLUDE_TRACK_DOC",
    "INTERVAL_MIN_DOC",
    "INTERVAL_S_DOC",
    "LOGIN_START_DESCRIPTION",
    "LOGIN_STATUS_DESCRIPTION",
    "PROFILE_DESCRIPTION",
    "SERIES_MAX_DAYS",
    "SLEEP_STAGES_MAX_DAYS",
    "START_DATE_DOC",
    "STATUS_DESCRIPTION",
    "WORKOUT_START_DOC",
    "parse_range",
    "parse_workout_start",
]

DAILY_MAX_DAYS = 92
"""The longest range of a daily tool: about a quarter, the CLI's ``--long`` threshold."""

CALORIES_MAX_DAYS = 31
"""The longest range of ``get_calories``: the service answers one day per request."""

SLEEP_STAGES_MAX_DAYS = 7
"""The longest range of ``get_sleep`` with its stage timelines."""

SERIES_MAX_DAYS = 7
"""The longest range of the dense tools (``get_heart_rate``, ``get_spo2``)."""

_CHECKED = "Figures were checked against the Mi Fitness app."


@dataclass(frozen=True)
class DataTool:
    """One data tool. ``method`` is the ``MiFit`` method it calls, and the
    ``mifit.chunks`` domain its range is read in chunks of; ``kind`` decides its
    parameters and output; ``max_days`` is its longest range (0: not a range)."""

    name: str
    method: str
    kind: Literal["daily", "sleep", "series", "detail"]
    max_days: int
    title: str
    description: str


def _days(max_days: int) -> str:
    return (
        "Days are local calendar days, start_date to end_date inclusive; "
        f"at most {max_days} days per call."
    )


DATA_TOOLS: tuple[DataTool, ...] = (
    DataTool(
        "get_steps",
        "steps",
        "daily",
        DAILY_MAX_DAYS,
        "Daily steps",
        "Daily step totals. One record per local day with data: date (YYYY-MM-DD), "
        "steps, distance_m (metres; null when not reported) and step_calories (the "
        "step report's own walking-calorie figure, kcal; it is NOT the app's calorie "
        "ring: use get_calories for that). "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_calories",
        "consumption",
        "daily",
        CALORIES_MAX_DAYS,
        "Daily calories",
        "Daily calories, in kcal. One record per local day with data: date, "
        "resting_kcal (resting, basal metabolic energy), active_kcal (energy spent on "
        "activity: the app's activity-calorie figure), intake_kcal (food logged; 0 is "
        "a real value) and total_kcal (resting plus active; null unless both are "
        "reported). The app composes its calorie ring on the phone, so the most "
        "recent day can read lower than the app's calorie ring. The service answers "
        "one day per request, so a call is slower than the other daily tools. "
        + _days(CALORIES_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_activity",
        "activity_daily",
        "daily",
        DAILY_MAX_DAYS,
        "Daily activity",
        "Daily activity figures as the app's activity page (活力指标) shows them. One "
        "record per local day with data: date, activity_count (the app's activity "
        "count, a count with no unit), activity_min (activity time, minutes) and "
        "active_kcal (calories spent on activity, kcal; the same figure as "
        "get_calories' active_kcal). 0 is a real value; null means not reported. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_heart_rate_daily",
        "heart_rate_daily",
        "daily",
        DAILY_MAX_DAYS,
        "Daily heart rate",
        "Daily heart-rate summary, in beats per minute. One record per local day with "
        "data: date, avg_bpm, max_bpm, min_bpm and resting_bpm (the day's resting "
        "heart rate); null means not reported. For the samples within a day use "
        "get_heart_rate. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_sleep",
        "sleep",
        "sleep",
        DAILY_MAX_DAYS,
        "Sleep",
        "Sleep per local day: every sleep session (a night or a nap) that ended on "
        "that day. Each record: date, asleep_min (minutes asleep across its sessions, "
        "time awake excluded), deep_min, light_min, rem_min and awake_min (stage "
        "minutes of the staged sessions only, so with an unstaged nap they add up to "
        "less than asleep_min; null when no session was staged), avg_hr_bpm, "
        "min_hr_bpm and max_hr_bpm (heart rate during sleep, bpm), avg_spo2_pct, "
        "min_spo2_pct and max_spo2_pct (blood oxygen during sleep, %), and sessions: "
        "each with start and end (local times), utc_offset_s (the offset of the zone "
        "it was recorded in), asleep_min and the same stage minutes. "
        "include_stages=true adds each session's stages timeline (start, end and "
        "stage: deep, light, rem, awake or unknown); with it a call covers at most "
        f"{SLEEP_STAGES_MAX_DAYS} days. The sleep score is not given: it could not be "
        "checked. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_spo2_daily",
        "spo2_daily",
        "daily",
        DAILY_MAX_DAYS,
        "Daily blood oxygen",
        "Daily blood-oxygen (SpO2) summary, in percent, as the service's daily report "
        "states it. One record per local day with data: date, avg_spo2_pct (the "
        "service's own average), max_spo2_pct and min_spo2_pct; null means not "
        "reported. For the readings within a day use get_spo2. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_stress_daily",
        "stress_daily",
        "daily",
        DAILY_MAX_DAYS,
        "Daily stress",
        "Daily stress summary. One record per local day with data: date, "
        "avg_stress_level, max_stress_level and min_stress_level (the stress level is "
        "a 1-100 index; the average is the service's own), and the minutes the day "
        "spent in each stress band, relaxed to severe: relaxed_min, mild_min, "
        "moderate_min and severe_min. 0 is a real value; null means not reported. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_weight",
        "weight",
        "daily",
        DAILY_MAX_DAYS,
        "Weight",
        "Weighings. One record per weighing in the range (records are sparse: only "
        "days with a weighing have one): time (local time it was recorded), weight_kg "
        "(body weight, kilograms) and bmi (the body-mass index stored with it; null "
        "when not reported). Body composition is not given: it is unverified. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_workouts",
        "workouts",
        "daily",
        DAILY_MAX_DAYS,
        "Workouts",
        "Recorded workouts that started in the range, newest first. Each: start and "
        "end (local times in the zone the workout was recorded in; end can be null), "
        "utc_offset_s (that zone's offset), sport (a name such as running or biking, "
        "in Health Connect's vocabulary; null when not mapped yet), "
        "source_sport_code (the service's own sport number), duration_s (recorded "
        "time in seconds, pauses not counted), distance_m (metres), active_kcal "
        "(energy burned by the activity), total_kcal (active plus the resting energy "
        "burned during the workout), avg_hr_bpm and max_hr_bpm (beats per minute) "
        "and steps. Pass a workout's start to get_workout_detail for its per-second "
        "samples. "
        + _days(DAILY_MAX_DAYS)
        + " "
        + _CHECKED,
    ),
    DataTool(
        "get_heart_rate",
        "heart_rate",
        "series",
        SERIES_MAX_DAYS,
        "Heart-rate samples",
        "Heart-rate samples through the day, for detail within a few days (use "
        "get_heart_rate_daily for daily figures). "
        + _days(SERIES_MAX_DAYS)
        + " Raw samples come as the columns time (local time) and bpm (beats per "
        "minute). More than 500 samples come as buckets instead, with the columns "
        "start (the bucket's local start), min_bpm, avg_bpm, max_bpm and count "
        "(samples in the bucket); resolution and notes say which. interval_min "
        "chooses the bucket size in minutes (0 for raw samples); a choice that gives "
        "more than 500 rows is an error. "
        + _CHECKED,
    ),
    DataTool(
        "get_spo2",
        "spo2",
        "series",
        SERIES_MAX_DAYS,
        "Blood-oxygen readings",
        "All-day blood-oxygen (SpO2) readings, for detail within a few days (use "
        "get_spo2_daily for daily figures). "
        + _days(SERIES_MAX_DAYS)
        + " Raw readings come as the columns time (local time) and spo2_pct "
        "(saturation, percent). More than 500 readings come as buckets instead, with "
        "the columns start (the bucket's local start), min_spo2_pct, avg_spo2_pct, "
        "max_spo2_pct and count (readings in the bucket); resolution and notes say "
        "which. interval_min chooses the bucket size in minutes (0 for raw "
        "readings); a choice that gives more than 500 rows is an error. Spot checks "
        "and low-SpO2 events are not included. "
        + _CHECKED,
    ),
    DataTool(
        "get_workout_detail",
        "workout_detail",
        "detail",
        0,
        "Workout detail",
        "The per-second detail of one workout: pass workout_start, the workout's "
        "start exactly as get_workouts gives it (or Unix seconds). Only outdoor runs "
        "and rides have a detail so far; other workouts come with a note and no "
        "samples. Gives workout (the same summary get_workouts gives: start, end, "
        "utc_offset_s, sport, source_sport_code, duration_s, distance_m, active_kcal, "
        "total_kcal, avg_hr_bpm, max_hr_bpm, steps), samples and track. samples has "
        "the columns time (local time), heart_rate_bpm, distance_m (cumulative "
        "metres), steps (cumulative), cadence_spm (steps per minute, running), "
        "pace_s_per_km (seconds per km, running) and speed_mps (metres per second, "
        "cycling); a gap in time is a pause. At most 500 rows: a longer workout is "
        "shown as one recorded second every few seconds (picked, not averaged), "
        "chosen automatically unless interval_s is given. track (the GPS route: time, "
        "latitude_deg, longitude_deg; at most 500 points) is only given when the "
        "server was started with --allow-gps and include_track is true: a track "
        "shows where the user was. "
        + _CHECKED,
    ),
)

STATUS_DESCRIPTION = (
    "Call this first. Gives today's local date and the time zone the dates are "
    "counted in, whether the user is logged in to Mi Fitness (with the age of the "
    "login, never the login itself), whether GPS tracks are enabled, and the server "
    "version. Reads nothing from the cloud."
)

LOGIN_START_DESCRIPTION = (
    "Log the user in to Mi Fitness by QR code, on this computer. Only needed when "
    "get_status says the user is not logged in, or a tool says the session expired. "
    "Returns the QR code as an image and its login_url: the user scans the code with "
    "the Xiaomi or Mi Fitness app and approves the login (or opens login_url on a phone "
    "signed in to the Xiaomi account). The QR image is also opened on this computer. "
    "Then call login_status. A second call while a login is waiting returns the same "
    "code."
)

LOGIN_STATUS_DESCRIPTION = (
    "How the QR login started by login_start stands: pending (waiting for the user to "
    "approve), succeeded (the data tools work now), failed, confirmation_required "
    "(Xiaomi wants a security confirmation; a page was opened on this computer) or "
    "expired (call login_start again)."
)

PROFILE_DESCRIPTION = (
    "The account profile as set in the app: height_cm (centimetres), weight_kg "
    "(kilograms; the weight stored in the profile), step_goal (daily step goal), "
    "calorie_goal_kcal (daily calorie goal, kcal) and activity_goal_min (daily "
    "activity-time goal, minutes); null when not set. Personal details (name, birth "
    "date, sex, account ids) are not given. " + _CHECKED
)

START_DATE_DOC = (
    "First day, a local calendar date in YYYY-MM-DD form (get_status gives today's date)."
)
END_DATE_DOC = "Last day, inclusive, in YYYY-MM-DD form; omit it for a single day."
INCLUDE_STAGES_DOC = (
    f"Add each sleep session's stage timeline (then at most {SLEEP_STAGES_MAX_DAYS} days "
    "per call)."
)
INTERVAL_MIN_DOC = (
    "Bucket size in minutes (0 for raw samples); omit it to let the server choose the "
    "smallest that fits in 500 rows."
)
WORKOUT_START_DOC = (
    "The workout's start as get_workouts gives it (such as 2026-10-01T10:00:00+08:00), "
    "or Unix seconds."
)
INTERVAL_S_DOC = (
    "Show one recorded second every this many seconds (1 for every second); omit it to "
    "let the server choose the smallest that fits in 500 rows."
)
INCLUDE_TRACK_DOC = (
    "Include the GPS track. Only possible when the server was started with --allow-gps; "
    "a track shows where the user was."
)

_DIGITS = re.compile(r"[0-9]+")


def _day(value: object, param: str) -> date:
    """``value`` as a local day whose first and last second this platform can
    represent (a read needs both), or a ``ToolFailure`` naming ``param``."""
    try:
        if not isinstance(value, str):
            raise ValueError(value)
        day = parse_day(value, param)
        to_epoch_s(value)
        to_epoch_s(value, end_of_day=True)
    except ValueError:
        bad = True
    else:
        bad = False
    if bad:
        raise ToolFailure(
            f"{param} must be a local calendar date in YYYY-MM-DD form, such as "
            f"2026-10-07 (got {value!r}); call get_status for today's date."
        )
    return day


def parse_range(
    start_date: str, end_date: str | None, *, max_days: int, tool: str
) -> tuple[date, date]:
    """The local days ``start_date`` to ``end_date`` (``start_date`` alone when
    ``end_date`` is ``None``), both inclusive and at most ``max_days`` of them;
    ``tool`` names the call in the message. Raises ``ToolFailure`` otherwise."""
    first = _day(start_date, "start_date")
    last = first if end_date is None else _day(end_date, "end_date")
    if last < first:
        raise ToolFailure("end_date must not be before start_date.")
    days = (last - first).days + 1
    if days > max_days:
        raise ToolFailure(
            f"{tool} reads at most {max_days} days per call; {first}..{last} is {days} "
            f"days. Split it into {math.ceil(days / max_days)} calls of up to "
            f"{max_days} days."
        )
    return first, last


def parse_workout_start(value: str | int) -> int:
    """A workout's start in Unix seconds: from an ISO 8601 date-time with an offset
    (as ``get_workouts`` gives it, or with ``Z``), or from Unix seconds (an int or a
    string of digits). Raises ``ToolFailure`` otherwise."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _DIGITS.fullmatch(text):
            return int(text)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            moment = None
        if moment is not None and moment.utcoffset() is not None:
            return int(moment.timestamp())
    raise ToolFailure(
        "workout_start must be a workout's start as get_workouts gives it, such as "
        "2026-10-01T10:00:00+08:00, or Unix seconds."
    )
