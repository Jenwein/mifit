"""Stress daily domain: one summary per local day from the daily report.

Each returned row is an outer object whose ``time`` is the day key and whose
``value`` is a JSON *string* (the day's figures and band durations, no time of its
own) that is decoded a second time here. The decoded value dicts go to
``DomainResult.raw`` untouched; ``records`` holds the normalized
``StressDailySummary`` view of each row (day from the outer ``time``, figures from
the value).

The day rules are those of steps (notes/06): the key is the UTC midnight of the
local date the row stands for, and the window is built from the caller's local
days (``days.report_window``).

The value is the app's ``CloudStressReport`` (``avg_stress``, ``max_stress``,
``min_stress`` and ``stress_scale``). One complete day checked against its raw
``stress`` points (notes/domain-catalog.md) gave the minimum, the maximum and the
rounded mean of those points, in 1-100. ``stress_scale`` (the time per stress band)
stays in ``raw`` only, because the unit of its durations is unverified. The figures
have not yet been compared with what the app shows.
"""

from __future__ import annotations

from datetime import tzinfo

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    as_int,
    as_positive_int,
    decode_value,
    register,
    stalled_note,
)
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.models.stress import StressDailySummary
from mifit.transport import Transport

_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
_VALUE_NOTES = {
    ValueProblem.UNREADABLE: (
        "Skipped a daily stress row whose value is missing or not JSON."
    ),
    ValueProblem.NOT_AN_OBJECT: (
        "Skipped a daily stress row whose value is not an object."
    ),
}


@register("stress_daily")
def fetch_stress_daily(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the daily stress summaries for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. Each record's ``date`` is its local date and
    ``epoch_s`` that date's local midnight.

    A window with no rows gives an empty result, never a fabricated figure. A row
    whose outer ``time`` is missing or unusable is skipped with a note (its decoded
    value is still kept in ``raw``). A figure that is absent, ``0``, negative or not
    a number becomes ``None``: ``0`` is treated as "not measured". A row whose
    figures are all missing still gives a record, with every figure ``None``. A
    read that stalled on an unusable cursor ends with a note saying days may be
    missing.
    """
    start_time, end_time = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))
    paged = t.get_paged_result(
        _ENDPOINT,
        {
            "tag": "daily_report",
            "key": "stress",
            "reverse": True,
            "limit": 50,
            "start_time": start_time,
            "end_time": end_time,
        },
        list_key="data_list",
    )

    result = DomainResult()
    for entry in paged.items:
        value, problem = decode_value(entry)
        if problem is not None:
            result.notes.append(_VALUE_NOTES[problem])
            continue

        result.raw.append(value)
        record = _to_record(entry, value, tz)
        if record is None:
            result.notes.append("Skipped a daily stress row without a usable day timestamp.")
            continue
        result.records.append(record)
    if paged.stalled:
        result.notes.append(stalled_note("daily stress rows"))
    return result


def _to_record(entry: dict, value: dict, tz: tzinfo | None) -> StressDailySummary | None:
    # The day key is the OUTER entry's ``time``; the decoded ``value`` carries no
    # time of its own. A bool, a string or a non-finite float is not a usable day key.
    key = as_int(entry.get("time"))
    if key is None:
        return None
    try:
        day = report_day(key)
        epoch_s = local_midnight(day, tz)
    except (ValueError, OverflowError, OSError):
        return None
    return StressDailySummary(
        date=day.isoformat(),
        epoch_s=epoch_s,
        avg_stress_level=as_positive_int(value.get("avg_stress")),
        max_stress_level=as_positive_int(value.get("max_stress")),
        min_stress_level=as_positive_int(value.get("min_stress")),
    )
