"""SpO2 daily domain: one summary per local day from the daily report.

Each returned row is an outer object whose ``time`` is the day key and whose
``value`` is a JSON *string* (the day's figures, no time of its own) that is
decoded a second time here. The decoded value dicts go to ``DomainResult.raw``
untouched; ``records`` holds the normalized ``Spo2DailySummary`` view of each row
(day from the outer ``time``, figures from the value).

The day rules are those of steps (notes/06): the key is the UTC midnight of the
local date the row stands for, and the window is built from the caller's local
days (``days.report_window``).

``avg_spo2_pct`` is the service's own daily average, deliberately not recomputed
from the day's points: the app's average is not their plain mean (notes/04 §4).
``lack_spo2_count`` and ``latest_spo2`` stay in ``raw`` only, because their
meaning is unverified; ``latest_spo2.time`` in particular is a shifted local
clock, not a true instant.
"""

from __future__ import annotations

import json
from datetime import tzinfo

from mifit.domains.base import DomainResult, as_int, as_positive_int, register
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.models.spo2 import Spo2DailySummary
from mifit.transport import Transport

_ENDPOINT = "data/get_aggregated_fitness_data_by_time"


@register("spo2_daily")
def fetch_spo2_daily(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the daily SpO2 summaries for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. Each record's ``date`` is its local date and
    ``epoch_s`` that date's local midnight.

    A window with no rows gives an empty result, never a fabricated figure. A row
    whose outer ``time`` is missing or unusable is skipped with a note (its decoded
    value is still kept in ``raw``). A figure that is absent, ``0``, negative or not
    a number becomes ``None``: ``0`` percent means "not measured". A row whose
    figures are all missing still gives a record, with every figure ``None``.
    """
    start_time, end_time = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))
    entries = t.get_paged(
        _ENDPOINT,
        {
            "tag": "daily_report",
            "key": "spo2",
            "reverse": True,
            "limit": 50,
            "start_time": start_time,
            "end_time": end_time,
        },
        list_key="data_list",
    )

    result = DomainResult()
    for entry in entries:
        try:
            value = json.loads(entry["value"])
        except (KeyError, TypeError, ValueError):
            result.notes.append("Skipped a daily SpO2 row whose value is missing or not JSON.")
            continue
        if not isinstance(value, dict):
            result.notes.append("Skipped a daily SpO2 row whose value is not an object.")
            continue

        result.raw.append(value)
        record = _to_record(entry, value, tz)
        if record is None:
            result.notes.append("Skipped a daily SpO2 row without a usable day timestamp.")
            continue
        result.records.append(record)
    return result


def _to_record(entry: dict, value: dict, tz: tzinfo | None) -> Spo2DailySummary | None:
    # The day key is the OUTER entry's ``time``; the decoded ``value`` carries no
    # time of its own (``latest_spo2.time`` is a shifted local clock, not the day).
    # A bool, a string or a non-finite float is not a usable day key.
    key = as_int(entry.get("time"))
    if key is None:
        return None
    try:
        day = report_day(key)
        epoch_s = local_midnight(day, tz)
    except (ValueError, OverflowError, OSError):
        return None
    return Spo2DailySummary(
        date=day.isoformat(),
        epoch_s=epoch_s,
        avg_spo2_pct=as_positive_int(value.get("avg_spo2")),
        max_spo2_pct=as_positive_int(value.get("max_spo2")),
        min_spo2_pct=as_positive_int(value.get("min_spo2")),
    )
