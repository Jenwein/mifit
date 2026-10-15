"""Activity daily domain: one summary per local day from three daily-report keys.

The app's activity page (活力指标) shows three figures a day, and each comes from a
daily-report key of its own: the activity count from ``valid_stand`` (the APK's
``CloudStandReport``, field ``count``), the activity time from ``intensity``
(``CloudHMStrengthReport``, field ``duration``) and the calories from ``calories``
(``CloudCaloriesReport``, field ``calories``). Each key is an ordinary daily
summary, read with ``daily_report.fetch_daily_summaries``: three requests, always
in that order (``READS``), whose records are then merged by local date.

The day rules are those of steps (notes/06): each row's key is the UTC midnight of
the local date it stands for, and the window is built from the caller's local days
(``days.report_window``).

On one complete local day checked against the raw keys (M7, notes/domain-catalog.md),
``count`` was the number of distinct local hours with a raw ``valid_stand``
interval (0-24), ``duration`` the number of raw ``intensity`` rows (0-1440) and
``calories`` equal to ``diet/user_consumption``'s ``exercise``, which
``consumption`` gives as ``active_kcal``. The app check (2026-10-15) then matched
all three against the activity page: ``count`` is its 活动次数 (activity count),
``duration`` its 活动时长 (activity time) in minutes and ``calories`` its 卡路里
(calories) in kcal.

``DomainResult.raw`` holds the decoded values untouched, one section per key in the
order above (the ``valid_stand`` values, then the ``intensity`` values, then the
``calories`` values), each in the service's order. Each section is the shared
reader's own ``raw``: a value whose row has no usable day keeps its place, which an
order by day could not give it. The notes follow the same key order. The APK also
declares ``goal`` (``CloudCaloriesReport``) and ``process``
(``CloudHMStrengthReport``); neither was seen live and their meaning is unverified,
so they stay in ``raw`` only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import tzinfo
from typing import NamedTuple

from mifit.domains.base import DomainResult, as_non_negative_int, register
from mifit.domains.daily_report import fetch_daily_summaries
from mifit.models.activity import ActivityDailySummary
from mifit.transport import Transport


class _Read(NamedTuple):
    key: str  # the daily-report key
    value_field: str  # the figure's field in the key's decoded value
    summary_field: str  # the ``ActivityDailySummary`` field it fills
    what: str  # the name of the key's rows in the notes


READS: tuple[_Read, ...] = (
    _Read("valid_stand", "count", "activity_count", "daily activity-count"),
    _Read("intensity", "duration", "activity_min", "daily activity-time"),
    _Read("calories", "calories", "active_kcal", "daily active-calorie"),
)
"""The three reads, in the order they are sent and their ``raw`` and notes appear."""


@register("activity_daily")
def fetch_activity_daily(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the daily activity summaries for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. Each record's ``date`` is its local date and
    ``epoch_s`` that date's local midnight.

    One request per key (``READS``). There is one record per local date that any
    key has a row for, newest first; a figure whose key has no row for that date is
    ``None``, and when a key has several rows for one date the first usable figure
    read gives it (a row whose figure is ``None`` never hides a later usable one;
    ``raw`` keeps every row). A window with no rows gives an empty result, never
    a fabricated figure. ``0`` is a real value for every figure (none that day); an
    absent, negative or non-number figure becomes ``None``.

    A row whose value is missing or unusable, or whose outer ``time`` is missing or
    unusable, is skipped with a note naming its figure (the decoded value of the
    latter is still kept in ``raw``). A read that stalled on an unusable cursor
    ends with a note saying that figure's rows may be missing. The notes come in
    key order.
    """
    result = DomainResult()
    epoch_of: dict[str, int] = {}
    figures_of: dict[str, dict[str, int | None]] = {}
    for read in READS:
        part = fetch_daily_summaries(
            t,
            read.key,
            start_epoch_s,
            end_epoch_s,
            what=read.what,
            summarize=_figure_reader(read.value_field),
            tz=tz,
        )
        result.raw.extend(part.raw)
        result.notes.extend(part.notes)
        for day, epoch_s, figure in part.records:
            epoch_of.setdefault(day, epoch_s)
            # The first usable figure read for a date gives that date's figure; a
            # row with none leaves the date present but does not hide a later one.
            day_figures = figures_of.setdefault(day, {})
            if day_figures.get(read.summary_field) is None:
                day_figures[read.summary_field] = figure

    for day in sorted(figures_of, reverse=True):  # ISO dates: newest first
        found = figures_of[day]
        figures = {read.summary_field: found.get(read.summary_field) for read in READS}
        result.records.append(
            ActivityDailySummary(date=day, epoch_s=epoch_of[day], **figures)
        )
    return result


def _figure_reader(
    value_field: str,
) -> Callable[[str, int, dict], tuple[str, int, int | None]]:
    """The ``summarize`` of one key's read: ``(day, epoch_s, figure)``, the figure
    being the value's ``value_field`` read with ``as_non_negative_int``."""

    def summarize(day: str, epoch_s: int, value: dict) -> tuple[str, int, int | None]:
        return day, epoch_s, as_non_negative_int(value.get(value_field))

    return summarize
