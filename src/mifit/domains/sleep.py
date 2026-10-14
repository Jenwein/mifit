"""Sleep domain: per-day sleep from the daily report.

Each daily report row (``tag=daily_report``, ``key=sleep``) stands for one local
date, keyed like every daily report (``days.report_window``/``report_day``; notes/06).
Its ``value`` is a JSON *string* holding the day's totals and ``segment_details``:
one entry per sleep session that ended on that day (a night, a nap). Session times
are true Unix seconds and ``timezone`` is the recording zone in 15-minute units. The
decoded value dicts go to ``DomainResult.raw`` untouched.

Fields whose meaning has not been verified against the app (``sleep_stage``,
``long_sleep_evaluation``, ``day_sleep_evaluation``, ``awake_count``) stay in
``raw`` only.
"""

from __future__ import annotations

import json
from datetime import tzinfo

from mifit.domains.base import DomainResult, register
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.models.sleep import SleepRecord, SleepSession
from mifit.transport import Transport

_DAILY_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
_TZ_UNIT_S = 900
"""``timezone`` counts quarter hours."""


@register("sleep")
def fetch_sleep(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the sleep of every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. A session belongs to the day the service files
    it under (the day it ended). Rows and sessions that cannot be read are skipped
    with a note, never fabricated.
    """
    start_time, end_time = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))
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
    return result


def _to_record(
    row: dict, value: dict, tz: tzinfo | None, notes: list[str]
) -> SleepRecord | None:
    try:
        day = report_day(int(row["time"]))
        asleep_min = int(value["total_duration"])
        epoch_s = local_midnight(day, tz)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None

    sessions = []
    for segment in value.get("segment_details") or []:
        session = _to_session(segment)
        if session is None:
            notes.append(f"Skipped a sleep session on {day.isoformat()} without usable times.")
            continue
        sessions.append(session)

    return SleepRecord(
        date=day.isoformat(),
        epoch_s=epoch_s,
        asleep_min=asleep_min,
        deep_min=_minutes(value.get("sleep_deep_duration")),
        light_min=_minutes(value.get("sleep_light_duration")),
        rem_min=_minutes(value.get("sleep_rem_duration")),
        awake_min=_minutes(value.get("sleep_awake_duration")),
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
    except (KeyError, TypeError, ValueError):
        return None
    zone = _minutes(segment.get("timezone"))
    return SleepSession(
        start_epoch_s=start,
        end_epoch_s=end,
        utc_offset_s=None if zone is None else zone * _TZ_UNIT_S,
        asleep_min=asleep_min,
        deep_min=_minutes(segment.get("sleep_deep_duration")),
        light_min=_minutes(segment.get("sleep_light_duration")),
        rem_min=_minutes(segment.get("sleep_rem_duration")),
        awake_min=_minutes(segment.get("sleep_awake_duration")),
    )


def _minutes(raw: object) -> int | None:
    """A reported whole number (``0`` included), or ``None`` when absent or unusable."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return int(raw)


def _measured(raw: object) -> int | None:
    """Like ``_minutes``, but ``0`` (the service's "not measured") is ``None`` too."""
    value = _minutes(raw)
    return None if value == 0 else value
