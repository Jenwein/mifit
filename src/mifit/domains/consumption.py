"""Consumption domain: daily resting / active / intake calories.

The endpoint answers one day per call (it is not paginated): the request carries a
single Unix-*seconds* ``time`` that falls inside the wanted day, and the business
data comes back under the ``result`` wrapper as ``{"basal", "exercise", "intake"}``
(resting, active and food kilocalories). This domain walks the UTC days of the
requested window, one call each, and maps each answer onto a ``ConsumptionRecord``.
The decoded ``result`` object of every answered day goes to ``DomainResult.raw``.
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
"""Offset from UTC midnight used as the request ``time``: safely inside the day."""


@register("consumption")
def fetch_consumption(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult:
    """Fetch the daily calorie figures for every UTC day from start to end.

    The first day is the UTC day containing ``start_epoch_s`` and the last is the one
    containing ``end_epoch_s`` (both inclusive); an ``end_epoch_s`` before
    ``start_epoch_s`` queries nothing. A day whose request fails with an ``ApiError``,
    or whose answer holds no usable figure, is skipped with a note and never
    fabricated as zeros; the decoded answer of such a day (when there is one) is
    still kept in ``raw``. A reported ``0`` (notably ``intake``) is a real value. Any
    other failure, such as an ``AuthError``, is not about one day and propagates.
    """
    result = DomainResult()
    day_start = start_epoch_s - start_epoch_s % _DAY_S
    last_day_start = end_epoch_s - end_epoch_s % _DAY_S
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
