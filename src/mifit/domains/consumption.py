"""Consumption domain: daily resting / active / intake calories.

The endpoint answers one day per call (it is not paginated): the request carries a
single Unix-*seconds* ``time`` naming the wanted day, and the business data comes back
under the ``result`` wrapper as ``{"basal", "exercise", "intake"}`` (resting, active
and food kilocalories). This domain walks the local calendar days of the requested
window, one call each, and maps each answer onto a ``ConsumptionRecord``. The decoded
``result`` object of every answered day goes to ``DomainResult.raw``.

Each day is requested by its *local* midnight, as the app does
(``TimeDateUtil.atStartOfDay``: the day's start in the device's default zone). The
service files a ``time`` under the day the app would mean by it, not under its UTC
day: live, for a UTC+8 account, a ``time`` at 12:00 UTC (20:00 local) was answered
with the *next* local day. Sending the local midnight sidesteps the service's day rule.
"""

from __future__ import annotations

import math
from datetime import date, timedelta, tzinfo

from mifit.domains.base import DomainResult, register
from mifit.domains.days import local_day_span, local_midnight
from mifit.errors import ApiError
from mifit.models.consumption import ConsumptionRecord
from mifit.transport import Transport

_ENDPOINT = "diet/user_consumption"


@register("consumption")
def fetch_consumption(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the daily calorie figures for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default (the app uses
    the phone's). The first day is the one holding ``start_epoch_s`` and the last the
    one holding ``end_epoch_s``, both inclusive; an end on an earlier day than the
    start queries nothing. Each record is labeled with its local ``date`` and carries
    that day's local midnight as ``epoch_s``.

    A day whose request fails with an ``ApiError``, or whose answer holds no usable
    figure, is skipped with a note and never fabricated as zeros; the decoded answer
    of such a day (when there is one) is still kept in ``raw``. A reported ``0``
    (notably ``intake``) is a real value. Any other failure, such as an
    ``AuthError``, is not about one day and propagates.
    """
    result = DomainResult()
    day, last_day = local_day_span(start_epoch_s, end_epoch_s, tz)
    while day <= last_day:
        _fetch_day(t, day, tz, result)
        day += timedelta(days=1)
    return result


def _fetch_day(t: Transport, day: date, tz: tzinfo | None, result: DomainResult) -> None:
    # Per date, not stepped by 86400 s, so every request stays on local midnight
    # across DST changes.
    midnight = local_midnight(day, tz)
    label = day.isoformat()
    try:
        payload = t.get_json(_ENDPOINT, {"time": midnight})
    except ApiError as err:
        suffix = "" if err.code is None else f" (code {err.code})"
        result.notes.append(f"Skipped {label}: the service returned an error{suffix}.")
        return

    # Business data lives under ``result``; fall back to the payload itself only
    # when there is no wrapper at all.
    data = payload.get("result", payload)
    if not isinstance(data, dict):
        result.notes.append(f"Skipped {label}: the response held no consumption object.")
        return

    result.raw.append(data)
    resting = _kcal(data.get("basal"))
    active = _kcal(data.get("exercise"))
    intake = _kcal(data.get("intake"))
    if resting is None and active is None and intake is None:
        result.notes.append(f"Skipped {label}: the response held no usable calorie figure.")
        return

    total = resting + active if resting is not None and active is not None else None
    result.records.append(
        ConsumptionRecord(
            date=label,
            epoch_s=midnight,
            resting_kcal=resting,
            active_kcal=active,
            intake_kcal=intake,
            total_kcal=total,
        )
    )


def _kcal(raw: object) -> float | None:
    """A reported figure as a float, or ``None`` when absent or not a real number."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None
