"""Consumption domain: daily resting / active / intake calories.

The endpoint answers one day per call (it is not paginated): the request carries a
single Unix-*seconds* ``time`` that falls inside the wanted day, and the business
data comes back under the ``result`` wrapper as ``{"basal", "exercise", "intake"}``
(resting, active and food kilocalories). This domain walks the UTC days of the
requested window, one call each, and maps each answer onto a ``ConsumptionRecord``.
The decoded ``result`` object of every answered day goes to ``DomainResult.raw``.

Day keys sit at UTC midnight, as the service keys its daily data. Each bound of the
window is mapped to the UTC midnight *nearest* to it, not the one at or before it: a
caller's bound is typically its *local* midnight (for UTC+8, 16:00 UTC the evening
before), which floors onto the wrong UTC day but rounds onto the intended date for any
timezone within +/-12h.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from mifit.domains.base import DomainResult, register
from mifit.errors import ApiError
from mifit.models.consumption import ConsumptionRecord
from mifit.transport import Transport

_ENDPOINT = "diet/user_consumption"
_DAY_S = 86400
_NOON_S = _DAY_S // 2
"""Half a day: the request ``time`` offset (safely inside the day) and the rounding
bias that maps a bound to the nearest UTC midnight."""


@register("consumption")
def fetch_consumption(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult:
    """Fetch the daily calorie figures for every UTC day from start to end.

    The first day is keyed by the UTC midnight nearest to ``start_epoch_s`` and the
    last by the one nearest to ``end_epoch_s`` (both inclusive), so a local-midnight
    bound maps to the local calendar date it names. Pass the end date's own midnight,
    not its last second: an end-of-day bound is nearer the *next* midnight and would
    include one extra day. An ``end_epoch_s`` that rounds to a day before the
    ``start_epoch_s`` day queries nothing. A day whose request fails with an
    ``ApiError``, or whose answer holds no usable figure, is skipped with a note and
    never fabricated as zeros; the decoded answer of such a day (when there is one) is
    still kept in ``raw``. A reported ``0`` (notably ``intake``) is a real value. Any
    other failure, such as an ``AuthError``, is not about one day and propagates.
    """
    result = DomainResult()
    day_start = ((start_epoch_s + _NOON_S) // _DAY_S) * _DAY_S
    last_day_start = ((end_epoch_s + _NOON_S) // _DAY_S) * _DAY_S
    while day_start <= last_day_start:
        _fetch_day(t, day_start, result)
        day_start += _DAY_S
    return result


def _fetch_day(t: Transport, day_start: int, result: DomainResult) -> None:
    date = datetime.fromtimestamp(day_start, tz=timezone.utc).date().isoformat()
    try:
        payload = t.get_json(_ENDPOINT, {"time": day_start + _NOON_S})
    except ApiError as err:
        suffix = "" if err.code is None else f" (code {err.code})"
        result.notes.append(f"Skipped {date}: the service returned an error{suffix}.")
        return

    # Business data lives under ``result``; fall back to the payload itself only
    # when there is no wrapper at all.
    data = payload.get("result", payload)
    if not isinstance(data, dict):
        result.notes.append(f"Skipped {date}: the response held no consumption object.")
        return

    result.raw.append(data)
    resting = _kcal(data.get("basal"))
    active = _kcal(data.get("exercise"))
    intake = _kcal(data.get("intake"))
    if resting is None and active is None and intake is None:
        result.notes.append(f"Skipped {date}: the response held no usable calorie figure.")
        return

    total = resting + active if resting is not None and active is not None else None
    result.records.append(
        ConsumptionRecord(
            date=date,
            epoch_s=day_start,
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
