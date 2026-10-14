"""Sleep domain: per-day sleep from the daily report, with stage timelines.

Each daily report row (``tag=daily_report``, ``key=sleep``) stands for one local
date, keyed like every daily report (``days.report_window``/``report_day``; notes/06).
Its ``value`` is a JSON *string* holding the day's totals and ``segment_details``:
one entry per sleep session that ended on that day (a night, a nap). Session times
are true Unix seconds and ``timezone`` is the recording zone in 15-minute units. The
decoded value dicts go to ``DomainResult.raw`` untouched.

The daily report carries no stage timeline; the raw ``sleep`` records
(``get_fitness_data_by_time``) do, as ``items`` of ``{start_time, end_time, state}``.
A raw session is attached to the daily session with the same bed and wake-up times.
Raw rows the service marks ``deleted`` are ignored. Of several raw rows with the same
times the first in service order is used, unless it has no stages and a later one has.
Each matched daily session gets its own copy of the stages. The raw read is widened by
a day on each side and filtered by that match, so its window bounds never decide a
day.

``DomainResult.raw`` holds the decoded daily values in service order, then the decoded
value of each raw session that matched a daily session, once each, in the order they
were served. Raw rows that matched nothing, duplicates and deleted rows are left out;
``MiFit.raw("sleep", start, end)`` returns every raw session as served.

Fields whose meaning has not been verified against the app (``sleep_stage``,
``long_sleep_evaluation``, ``day_sleep_evaluation``, ``awake_count``) stay in
``raw`` only.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date, timedelta, tzinfo
from typing import NamedTuple

from mifit.domains.base import DomainResult, register
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.errors import ApiError
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.transport import Transport

_DAILY_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
_RAW_ENDPOINT = "data/get_fitness_data_by_time"
_DAY_S = 86400
_STAGES = {2: "deep", 3: "light", 4: "rem", 5: "awake"}
"""Stage codes: 2/3/5 checked against reported durations on a real night; 4 (REM)
from the APK's constants only. Any other code is ``"unknown"``."""
_TZ_UNIT_S = 900
"""``timezone`` counts quarter hours."""


class _RawSession(NamedTuple):
    """One decoded raw session: its value, its readable stages and the number of stage
    items that could not be read."""

    value: dict
    stages: list[SleepStage]
    skipped: int


@register("sleep")
def fetch_sleep(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the sleep of every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. A session belongs to the day the service files
    it under (the day it ended). Rows and sessions that cannot be read are skipped
    with a note, never fabricated. The raw read for stage timelines is skipped when no
    day has a session. If it fails with an ``ApiError`` the days stand without stages
    and one note says so; any other failure, such as an ``AuthError``, propagates.
    """
    first, last = local_day_span(start_epoch_s, end_epoch_s, tz)
    start_time, end_time = report_window(first, last)
    rows = t.get_paged(
        _DAILY_ENDPOINT,
        {
            "tag": "daily_report",
            "key": "sleep",
            "reverse": True,
            "limit": 50,
            "start_time": start_time,
            "end_time": end_time,
        },
        list_key="data_list",
    )

    result = DomainResult()
    for row in rows:
        try:
            value = json.loads(row["value"])
        except (KeyError, TypeError, ValueError):
            result.notes.append("Skipped a daily sleep row whose value is missing or not valid JSON.")
            continue
        if not isinstance(value, dict):
            result.notes.append("Skipped a daily sleep row whose value is not an object.")
            continue

        result.raw.append(value)
        record = _to_record(row, value, tz, result.notes)
        if record is None:
            result.notes.append(
                "Skipped a daily sleep row without a usable day timestamp or total duration."
            )
            continue
        result.records.append(record)

    _attach_stages(t, first, last, tz, result)
    return result


def _attach_stages(
    t: Transport, first: date, last: date, tz: tzinfo | None, result: DomainResult
) -> None:
    if not any(record.sessions for record in result.records):
        return
    try:
        rows = t.get_paged(
            _RAW_ENDPOINT,
            {
                "key": "sleep",
                "start_time": local_midnight(first, tz) - _DAY_S,
                "end_time": local_midnight(last + timedelta(days=1), tz) + _DAY_S,
                "reverse": True,
            },
            list_key="data_list",
        )
    except ApiError as err:
        suffix = "" if err.code is None else f" (code {err.code})"
        result.notes.append(
            f"Stage timelines were not read: the service returned an error{suffix}. "
            "Sleep sessions have no stages."
        )
        return

    timelines: dict[tuple[int, int], _RawSession] = {}
    for row in rows:
        try:
            value = json.loads(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(value, dict) or row.get("deleted"):
            continue
        try:
            key = (int(value["bedtime"]), int(value["wake_up_time"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        stages, skipped = _to_stages(value.get("items"))
        kept = timelines.get(key)
        # The first row wins, unless it has no stages and this one has. Re-assigning
        # an existing key keeps its place, so ``timelines`` stays in first-seen order.
        if kept is None or (stages and not kept.stages):
            timelines[key] = _RawSession(value, stages, skipped)

    matched: set[tuple[int, int]] = set()
    for record in result.records:
        unmatched = False
        for session in record.sessions:
            key = (session.start_epoch_s, session.end_epoch_s)
            raw = timelines.get(key)
            if raw is None:
                unmatched = True
                continue
            matched.add(key)
            session.stages = [dataclasses.replace(stage) for stage in raw.stages]
            if raw.skipped:
                result.notes.append(
                    f"Skipped {raw.skipped} unreadable stage item(s) of a sleep session "
                    f"on {record.date}."
                )
        if unmatched:
            result.notes.append(
                f"Stage timeline not found for a sleep session on {record.date}."
            )

    result.raw.extend(raw.value for key, raw in timelines.items() if key in matched)


def _to_stages(items: object) -> tuple[list[SleepStage], int]:
    """The readable stages of a raw session and how many items were skipped.

    No ``items`` is a nap (nothing skipped); anything but a list counts as one
    skipped item. Inside a list, an item that is not an object or whose start or end
    is missing, non-numeric or non-finite is skipped.
    """
    if items is None:
        return [], 0
    if not isinstance(items, list):
        return [], 1
    stages = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            start = int(item.get("start_time", item.get("start")))
            end = int(item.get("end_time", item.get("end")))
        except (TypeError, ValueError, OverflowError):
            skipped += 1
            continue
        stages.append(SleepStage(start, end, _stage_name(item.get("state"))))
    return stages, skipped


def _stage_name(state: object) -> str:
    """The stage for a wire code; anything unknown or unreadable is ``"unknown"``."""
    if isinstance(state, int) and not isinstance(state, bool):
        return _STAGES.get(state, "unknown")
    return "unknown"


def _to_record(
    row: dict, value: dict, tz: tzinfo | None, notes: list[str]
) -> SleepRecord | None:
    try:
        day = report_day(int(row["time"]))
        asleep_min = int(value["total_duration"])
        epoch_s = local_midnight(day, tz)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None

    segments = value.get("segment_details")
    if segments is None:
        segments = []
    elif not isinstance(segments, list):
        notes.append(f"Skipped the sessions of {day.isoformat()}: they are not a list.")
        segments = []

    sessions = []
    for segment in segments:
        session = _to_session(segment)
        if session is None:
            notes.append(f"Skipped a sleep session on {day.isoformat()} without usable times.")
            continue
        sessions.append(session)

    deep, light, rem, awake = _stage_minutes(value, asleep_min)
    return SleepRecord(
        date=day.isoformat(),
        epoch_s=epoch_s,
        asleep_min=asleep_min,
        deep_min=deep,
        light_min=light,
        rem_min=rem,
        awake_min=awake,
        score=_measured(value.get("sleep_score")),
        avg_hr_bpm=_measured(value.get("avg_hr")),
        min_hr_bpm=_measured(value.get("min_hr")),
        max_hr_bpm=_measured(value.get("max_hr")),
        avg_spo2_pct=_measured(value.get("avg_spo2")),
        min_spo2_pct=_measured(value.get("min_spo2")),
        max_spo2_pct=_measured(value.get("max_spo2")),
        sessions=sessions,
    )


def _to_session(segment: object) -> SleepSession | None:
    if not isinstance(segment, dict):
        return None
    try:
        start = int(segment["bedtime"])
        end = int(segment["wake_up_time"])
        asleep_min = int(segment["duration"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    zone = _minutes(segment.get("timezone"))
    deep, light, rem, awake = _stage_minutes(segment, asleep_min)
    return SleepSession(
        start_epoch_s=start,
        end_epoch_s=end,
        utc_offset_s=None if zone is None else zone * _TZ_UNIT_S,
        asleep_min=asleep_min,
        deep_min=deep,
        light_min=light,
        rem_min=rem,
        awake_min=awake,
    )


def _stage_minutes(
    value: dict, asleep_min: int
) -> tuple[int | None, int | None, int | None, int | None]:
    """Deep, light, REM and awake minutes of a day or session.

    Sleep that was never staged (a nap) is reported with every stage at ``0`` although
    ``asleep_min`` is not: those zeros mean "no breakdown", so all four are ``None``.
    """
    minutes = tuple(
        _minutes(value.get(key))
        for key in (
            "sleep_deep_duration",
            "sleep_light_duration",
            "sleep_rem_duration",
            "sleep_awake_duration",
        )
    )
    if asleep_min > 0 and not any(minutes):
        return None, None, None, None
    return minutes


def _minutes(raw: object) -> int | None:
    """A reported whole number (``0`` included), or ``None`` when absent or unusable
    (not a number, or a float that is not finite)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    return int(raw)


def _measured(raw: object) -> int | None:
    """Like ``_minutes``, but ``0`` (the service's "not measured") is ``None`` too."""
    value = _minutes(raw)
    return None if value == 0 else value
