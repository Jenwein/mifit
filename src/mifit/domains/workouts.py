"""Workouts domain: one summary per recorded workout from the sport-records endpoint.

Each returned row is an outer object whose ``time`` is the workout's start instant
and whose ``value`` is a JSON *string* (the workout's figures) that is decoded a
second time here. The decoded value dicts go to ``DomainResult.raw`` untouched;
``records`` holds the normalized ``Workout`` view of each one.

The service selects workouts by start instant with an exclusive start: it returns
the rows whose outer ``time`` satisfies ``start_time < time <= end_time`` (notes/10),
the same rule as the daily reports (notes/06). ``fetch_workouts`` therefore asks
from one second before the caller's start and keeps only the rows whose outer
``time`` lies in ``[start_epoch_s, end_epoch_s]``; a workout starting exactly at
the caller's start is kept.

Records come newest first, as the service sends them. A row marked deleted is
ignored. The read follows the service's cursor for at most ``_MAX_PAGES`` pages,
and when that cap cuts it short the result carries a note saying so; so does a read
that stalls on an unusable cursor.

``_SPORTS`` covers only the sport types verified so far and is the place to add
more: one entry here plus one line in the sport-type test (notes/10 §4). A type
with no entry still gives a record, with ``sport=None`` and its number in
``source_sport_code``.

Only verified fields reach ``Workout``. Everything else stays in ``raw`` because
its meaning is unverified (design §2): the minimum heart rate, the total energy
(it adds resting energy; which of the two energy figures the app shows is still to
check), the heart-rate zone durations, pace, speed, cadence and stride figures,
training-effect and load figures, and the format and version numbers. See
notes/10 for the shapes and the open points.
"""

from __future__ import annotations

import json

from mifit.domains.base import (
    DomainResult,
    as_int,
    as_positive_int,
    register,
    stalled_note,
)
from mifit.models.workouts import Workout
from mifit.transport import Transport

_ENDPOINT = "data/get_sport_records_by_time"
_MAX_PAGES = 20
_TZ_UNIT_S = 900
"""The source counts the recording zone's offset in quarter hours."""

_SPORTS = {
    1: "running",
    6: "biking",
    300: "stair_climbing_machine",
    308: "strength_training",
}
"""Source sport-type number -> neutral name (Health Connect exercise types)."""


@register("workouts")
def fetch_workouts(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult:
    """Fetch the workouts that start between two Unix-second timestamps, both
    inclusive, newest first.

    The request starts one second early because the service excludes its
    ``start_time``; only rows whose outer ``time`` is within
    ``[start_epoch_s, end_epoch_s]`` are kept, and the rows outside it, like deleted
    ones, go without a note. A row whose outer ``time`` is missing or unusable, or
    whose value is not a JSON object, is skipped with a note and left out of
    ``raw``. A workout with no usable start time is skipped with a note (its decoded
    value is still kept in ``raw``). A figure that is absent, ``0``, negative or not
    a number becomes ``None``: ``0`` means "not reported". A window with no rows
    gives an empty result, never a fabricated workout.
    """
    paged = t.get_paged_result(
        _ENDPOINT,
        {
            "start_time": start_epoch_s - 1,
            "end_time": end_epoch_s,
            "reverse": True,
            "limit": 50,
        },
        list_key="sport_records",
        max_pages=_MAX_PAGES,
    )

    result = DomainResult()
    for entry in paged.items:
        # Which window the row belongs to is decided by the OUTER ``time``, the
        # workout's start instant.
        outer_time = as_int(entry.get("time")) if isinstance(entry, dict) else None
        if outer_time is None:
            result.notes.append("Skipped a workout row without a usable time.")
            continue
        if not start_epoch_s <= outer_time <= end_epoch_s:
            continue
        if entry.get("deleted"):
            continue

        try:
            value = json.loads(entry["value"])
        except (KeyError, TypeError, ValueError):
            result.notes.append(
                "Skipped a workout row whose value is missing or not JSON."
            )
            continue
        if not isinstance(value, dict):
            result.notes.append("Skipped a workout row whose value is not an object.")
            continue

        result.raw.append(value)
        workout = _to_workout(value)
        if workout is None:
            result.notes.append("Skipped a workout without a usable start time.")
            continue
        result.records.append(workout)

    if paged.truncated:
        result.notes.append(
            f"Workout list stopped at its {_MAX_PAGES}-page cap while the service had "
            "more: the oldest workouts in the range are missing; ask for a shorter range."
        )
    if paged.stalled:
        result.notes.append(stalled_note("workout rows"))
    return result


def _to_workout(value: dict) -> Workout | None:
    start = as_positive_int(value.get("start_time"))
    if start is None:
        return None
    zone_units = as_int(value.get("timezone"))
    sport_code = as_int(value.get("sport_type"))
    return Workout(
        start_epoch_s=start,
        end_epoch_s=as_positive_int(value.get("end_time")),
        # ``0`` is a real offset (UTC), so it is kept; only a missing one is None.
        utc_offset_s=None if zone_units is None else zone_units * _TZ_UNIT_S,
        sport=_SPORTS.get(sport_code),
        source_sport_code=sport_code,
        duration_s=as_positive_int(value.get("duration")),
        distance_m=as_positive_int(value.get("distance")),
        active_kcal=as_positive_int(value.get("calories")),
        avg_hr_bpm=as_positive_int(value.get("avg_hrm")),
        max_hr_bpm=as_positive_int(value.get("max_hrm")),
        steps=as_positive_int(value.get("steps")),
    )
