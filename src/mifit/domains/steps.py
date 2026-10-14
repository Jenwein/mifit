"""Steps domain: daily step summaries from the aggregated fitness endpoint.

Each returned row is an outer object whose ``time`` is the UTC-midnight day label
and whose ``value`` is a JSON *string* (``{"steps", "distance", "calories"}``, no
time of its own) that is decoded a second time here. The decoded value dicts go to
``DomainResult.raw`` untouched; ``records`` holds the normalized ``StepRecord``
view of each row (day from the outer ``time``, figures from the value).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from mifit.domains.base import DomainResult, register
from mifit.models.steps import StepRecord
from mifit.transport import Transport

_ENDPOINT = "data/get_aggregated_fitness_data_by_time"


@register("steps")
def fetch_steps(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult:
    """Fetch the daily step summaries between two Unix-second timestamps.

    A window with no rows gives an empty result, never a fabricated zero. A row
    whose outer ``time`` or whose value's ``steps`` is missing or unusable is
    skipped with a note (its decoded value is still kept in ``raw``); an absent
    ``distance``/``calories`` becomes ``None``.
    """
    entries = t.get_paged(
        _ENDPOINT,
        {
            "tag": "daily_report",
            "key": "steps",
            "reverse": True,
            "limit": 50,
            "start_time": start_epoch_s,
            "end_time": end_epoch_s,
        },
        list_key="data_list",
    )

    result = DomainResult()
    for entry in entries:
        try:
            value = json.loads(entry["value"])
        except (KeyError, TypeError, ValueError):
            result.notes.append(
                "Skipped a daily step row whose value is missing or not valid JSON."
            )
            continue
        if not isinstance(value, dict):
            result.notes.append("Skipped a daily step row whose value is not an object.")
            continue

        result.raw.append(value)
        record = _to_record(entry, value)
        if record is None:
            result.notes.append(
                "Skipped a daily step row without a usable day timestamp or step count."
            )
            continue
        result.records.append(record)
    return result


def _to_record(entry: dict, value: dict) -> StepRecord | None:
    # The day timestamp is the OUTER entry's ``time``; the decoded ``value`` holds
    # only ``steps``/``distance``/``calories`` and carries no time of its own.
    try:
        epoch_s = int(entry["time"])
        steps = int(value["steps"])
        distance_m = _optional_int(value.get("distance"))
        calories = _optional_int(value.get("calories"))
        # The service labels each day with its UTC midnight, so the UTC calendar
        # date is the day the record stands for.
        date = datetime.fromtimestamp(epoch_s, tz=timezone.utc).date().isoformat()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return StepRecord(
        date=date,
        steps=steps,
        distance_m=distance_m,
        calories=calories,
        epoch_s=epoch_s,
    )


def _optional_int(raw: object) -> int | None:
    return None if raw is None else int(raw)
