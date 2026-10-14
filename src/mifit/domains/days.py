"""Day semantics shared by the per-day domains.

A caller's days are *local* calendar days, in the machine's zone by default (the app
uses the phone's). The service has two ways of naming a day, both taken from the
app (notes/06, and the consumption verification):

* A daily report's ``time`` (``get_aggregated_fitness_data_by_time``,
  ``tag=daily_report``) is the UTC midnight of the local date it stands for, in any
  zone, and a time window returns the rows with ``start_time < time <= end_time``.
* ``diet/user_consumption`` takes the local midnight of the wanted day.

Callers name a day as a ``"YYYY-MM-DD"`` string, read by ``parse_day``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo

_ONE_DAY = timedelta(days=1)
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_day(value: str, what: str = "date") -> date:
    """The day a ``"YYYY-MM-DD"`` string names.

    Raises ``ValueError`` for a string of any other shape (``date.fromisoformat``
    alone would also take ``"20260101"`` or a week date) and for a day that does
    not exist; ``what`` names the argument in the message.
    """
    if not _ISO_DAY.fullmatch(value):
        raise ValueError(f"{what} must look like 'YYYY-MM-DD', got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        # The original only repeats the caller's own argument.
        raise ValueError(f"{what} {value!r} is not a calendar date") from None


def local_day_span(
    start_epoch_s: int, end_epoch_s: int, tz: tzinfo | None = None
) -> tuple[date, date]:
    """The local dates holding ``start_epoch_s`` and ``end_epoch_s``, in ``tz``
    (the machine's zone when ``None``)."""
    return (
        datetime.fromtimestamp(start_epoch_s, tz).date(),
        datetime.fromtimestamp(end_epoch_s, tz).date(),
    )


def local_midnight(day: date, tz: tzinfo | None = None) -> int:
    """Unix seconds of ``day``'s first instant in ``tz`` (the machine's zone when
    ``None``), computed per date so DST changes are honored."""
    return int(datetime.combine(day, time(), tz).timestamp())


def report_window(first: date, last: date) -> tuple[int, int]:
    """``(start_time, end_time)`` selecting the daily reports of ``first`` through
    ``last``: the service excludes ``start_time``, so it is the day before's key."""
    return _utc_midnight(first - _ONE_DAY), _utc_midnight(last)


def report_day(report_time: int) -> date:
    """The local date a daily report's ``time`` stands for: its UTC date."""
    return datetime.fromtimestamp(report_time, timezone.utc).date()


def _utc_midnight(day: date) -> int:
    return int(datetime.combine(day, time(), timezone.utc).timestamp())
