"""Steps domain: daily step summaries from the aggregated fitness endpoint.

Each returned row is an outer object whose ``time`` is the day key and whose
``value`` is a JSON *string* (``{"steps", "distance", "calories"}``, no time of its
own) that is decoded a second time here. The decoded value dicts go to
``DomainResult.raw`` untouched; ``records`` holds the normalized ``StepRecord``
view of each row (day from the outer ``time``, figures from the value).

The day key is the UTC midnight of the local date the row stands for, in any zone,
and the service returns keys in ``(start_time, end_time]`` (notes/06); the window
is built from the caller's local days accordingly (``days.report_window``).
"""

from __future__ import annotations

from datetime import tzinfo

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    decode_value,
    register,
    stalled_note,
)
from mifit.domains.days import local_day_span, local_midnight, report_day, report_window
from mifit.models.steps import StepRecord
from mifit.transport import Transport

_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
_VALUE_NOTES = {
    ValueProblem.UNREADABLE: (
        "Skipped a daily step row whose value is missing or not valid JSON."
    ),
    ValueProblem.NOT_AN_OBJECT: (
        "Skipped a daily step row whose value is not an object."
    ),
}


@register("steps")
def fetch_steps(
    t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None
) -> DomainResult:
    """Fetch the daily step summaries for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive. Each record's ``date`` is its local date and
    ``epoch_s`` that date's local midnight.

    A window with no rows gives an empty result, never a fabricated zero. A row
    whose outer ``time`` or whose value's ``steps`` is missing or unusable is
    skipped with a note (its decoded value is still kept in ``raw``); an absent
    ``distance``/``calories`` becomes ``None``. A read that stalled on an unusable
    cursor ends with a note saying days may be missing.
    """
    start_time, end_time = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))
    paged = t.get_paged_result(
        _ENDPOINT,
        {
            "tag": "daily_report",
            "key": "steps",
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
            result.notes.append(
                "Skipped a daily step row without a usable day timestamp or step count."
            )
            continue
        result.records.append(record)
    if paged.stalled:
        result.notes.append(stalled_note("daily step rows"))
    return result


def _to_record(entry: dict, value: dict, tz: tzinfo | None) -> StepRecord | None:
    # The day key is the OUTER entry's ``time``; the decoded ``value`` holds only
    # ``steps``/``distance``/``calories`` and carries no time of its own.
    try:
        day = report_day(int(entry["time"]))
        steps = int(value["steps"])
        distance_m = _optional_int(value.get("distance"))
        # The wire key is ``calories``; the model calls it ``step_calories`` because
        # it is only the step report's walking calories, not the app's calorie ring.
        step_calories = _optional_int(value.get("calories"))
        epoch_s = local_midnight(day, tz)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return StepRecord(
        date=day.isoformat(),
        steps=steps,
        distance_m=distance_m,
        step_calories=step_calories,
        epoch_s=epoch_s,
    )


def _optional_int(raw: object) -> int | None:
    return None if raw is None else int(raw)
