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

``sport_rows`` does this list read for ``workouts`` and for the workout-detail
domain, so both select the same workouts with the same notes. It also keeps each
row's outer ``sid`` (the source device's id, which names the workout's detail
files) in memory; ``workouts`` never outputs it.

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

from dataclasses import dataclass, field

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    as_int,
    as_positive_int,
    decode_value,
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
_VALUE_NOTES = {
    ValueProblem.UNREADABLE: (
        "Skipped a workout row whose value is missing or not JSON."
    ),
    ValueProblem.NOT_AN_OBJECT: "Skipped a workout row whose value is not an object.",
}
NO_START_NOTE = "Skipped a workout without a usable start time."
"""The note for a workout whose summary has no usable start time; the workout-detail
domain gives the same one."""


@dataclass(frozen=True)
class SportRow:
    """One row of the workout list as ``sport_rows`` keeps it: a decoded value, or the
    note for a row that was skipped."""

    value: dict | None
    """The decoded value (the object ``raw`` gets), or ``None`` when the row was
    skipped."""
    note: str | None = None
    """Why the row was skipped: the exact note ``fetch_workouts`` gives for it."""
    sid: str | None = field(default=None, repr=False)
    """The outer row's ``sid`` when it is a non-empty string, else ``None`` (always
    ``None`` for a skipped row). It names the workout's FDS files and is never
    output."""


@dataclass(frozen=True)
class SportRows:
    """What ``sport_rows`` read."""

    rows: list[SportRow]
    """In service order (newest first); rows outside the window and deleted rows are
    absent."""
    end_notes: list[str]
    """The page-cap and stalled-cursor notes, which follow every row's own note."""


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
    read = sport_rows(t, start_epoch_s, end_epoch_s)

    result = DomainResult()
    for row in read.rows:
        if row.value is None:
            result.notes.append(row.note)
            continue
        result.raw.append(row.value)
        workout = to_workout(row.value)
        if workout is None:
            result.notes.append(NO_START_NOTE)
            continue
        result.records.append(workout)

    result.notes.extend(read.end_notes)
    return result


def sport_rows(t: Transport, start_epoch_s: int, end_epoch_s: int) -> SportRows:
    """Read the workout list for ``fetch_workouts`` and the workout-detail domain.

    One paged read, at most ``_MAX_PAGES`` pages, from one second before
    ``start_epoch_s``; the rows whose outer ``time`` lies in
    ``[start_epoch_s, end_epoch_s]`` and that are not deleted are decoded, in service
    order. A row whose outer ``time`` is missing or unusable, or whose value is not a
    JSON object, stays in place as a ``SportRow`` with no value and its note.
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

    rows = []
    for entry in paged.items:
        # Which window the row belongs to is decided by the OUTER ``time``, the
        # workout's start instant.
        outer_time = as_int(entry.get("time")) if isinstance(entry, dict) else None
        if outer_time is None:
            rows.append(SportRow(None, "Skipped a workout row without a usable time."))
            continue
        if not start_epoch_s <= outer_time <= end_epoch_s:
            continue
        if entry.get("deleted"):
            continue

        value, problem = decode_value(entry)
        if problem is not None:
            rows.append(SportRow(None, _VALUE_NOTES[problem]))
            continue

        sid = entry.get("sid")
        rows.append(SportRow(value, sid=sid if isinstance(sid, str) and sid else None))

    end_notes = []
    if paged.truncated:
        end_notes.append(
            f"Workout list stopped at its {_MAX_PAGES}-page cap while the service had "
            "more: the oldest workouts in the range are missing; ask for a shorter range."
        )
    if paged.stalled:
        end_notes.append(stalled_note("workout rows"))
    return SportRows(rows, end_notes)


def to_workout(value: dict) -> Workout | None:
    """The ``Workout`` of a decoded workout value, or ``None`` when it has no usable
    start time."""
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
