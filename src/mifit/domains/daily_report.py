"""The daily report: one row per local day from ``get_aggregated_fitness_data_by_time``.

Each row is an outer object whose ``time`` is the day key and whose ``value`` is a
JSON *string* (the day's figures, no time of its own). The key is the UTC midnight
of the local date the row stands for, in any zone, and a window selects the rows
with ``start_time < time <= end_time`` (notes/06); ``days.report_window`` builds it
from the caller's local days.

``read_daily_report`` is the one request: the rows of a key (and tag) for a span of
local days, as the service sent them. ``passthrough.fetch_daily_rows`` and ``steps``
read their rows through it.

``fetch_daily_summaries`` is the whole fetch of a daily summary domain
(``heart_rate_daily``, ``spo2_daily`` and ``stress_daily``, plus ``activity_daily``,
which calls it once per key): it decodes each row, keeps the decoded values in
``raw``, finds each row's day and leaves the record itself to the domain's
``summarize``. Its notes are worded with the domain's ``what``.

``steps`` and ``sleep`` keep their own loops, because their notes and their day
parsing differ: they read the outer ``time`` with ``int()``, and a row also needs
its main figure (the step count, the sleep duration) to give a record.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import tzinfo
from typing import Any

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    as_int,
    decode_value,
    stalled_note,
)
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.transport import PagedResult, Transport

AGGREGATED_ENDPOINT = "data/get_aggregated_fitness_data_by_time"


def read_daily_report(
    t: Transport,
    key: str,
    start_epoch_s: int,
    end_epoch_s: int,
    *,
    tz: tzinfo | None = None,
    tag: str = "daily_report",
) -> PagedResult:
    """Read the ``tag`` rows of ``key`` for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. One paged read, newest first; the rows come
    back as the service sent them, with nothing checked or decoded.
    """
    start_time, end_time = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))
    return t.get_paged_result(
        AGGREGATED_ENDPOINT,
        {
            "tag": tag,
            "key": key,
            "reverse": True,
            "limit": 50,
            "start_time": start_time,
            "end_time": end_time,
        },
        list_key="data_list",
    )


def fetch_daily_summaries(
    t: Transport,
    key: str,
    start_epoch_s: int,
    end_epoch_s: int,
    *,
    what: str,
    summarize: Callable[[str, int, dict], Any],
    tz: tzinfo | None = None,
) -> DomainResult:
    """Fetch the daily-report rows of ``key`` and give one record per usable row.

    The rows are read with ``read_daily_report``, in the service's order. A row whose
    value is missing, not JSON or not an object is skipped with a note, and nothing
    of it goes to ``raw``. Every other row's decoded value goes to ``raw``
    untouched. A row whose outer ``time`` is missing or unusable (a bool, a string,
    a non-finite float, a key out of range) is then skipped with a note; otherwise
    its record is ``summarize(day, epoch_s, value)``, where ``day`` is the local
    date as ``"YYYY-MM-DD"`` and ``epoch_s`` that date's local midnight in ``tz``.
    A read that stalled on an unusable cursor ends with a note saying rows may be
    missing.

    ``what`` names the rows in the notes (e.g. ``"daily heart-rate"``).
    """
    value_notes = {
        ValueProblem.UNREADABLE: (
            f"Skipped a {what} row whose value is missing or not JSON."
        ),
        ValueProblem.NOT_AN_OBJECT: (
            f"Skipped a {what} row whose value is not an object."
        ),
    }
    paged = read_daily_report(t, key, start_epoch_s, end_epoch_s, tz=tz)

    result = DomainResult()
    for entry in paged.items:
        value, problem = decode_value(entry)
        if problem is not None:
            result.notes.append(value_notes[problem])
            continue

        result.raw.append(value)
        found = _day_of(entry, tz)
        if found is None:
            result.notes.append(f"Skipped a {what} row without a usable day timestamp.")
            continue
        day, epoch_s = found
        result.records.append(summarize(day, epoch_s, value))
    if paged.stalled:
        result.notes.append(stalled_note(f"{what} rows"))
    return result


def _day_of(entry: dict, tz: tzinfo | None) -> tuple[str, int] | None:
    """The local date a row stands for, as ``"YYYY-MM-DD"``, and that date's local
    midnight in ``tz``; ``None`` when the row's outer ``time`` is not a usable day key.
    """
    # The day key is the OUTER entry's ``time``; the decoded value carries no time of
    # its own. A bool, a string or a non-finite float is not a usable day key.
    report_time = as_int(entry.get("time"))
    if report_time is None:
        return None
    try:
        day = report_day(report_time)
        epoch_s = local_midnight(day, tz)
    except (ValueError, OverflowError, OSError):
        return None
    return day.isoformat(), epoch_s
