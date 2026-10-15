"""Dense data as column blocks of at most 500 rows.

A block is ``{"resolution", "columns", "rows"}``: the rows are lists in the order of
``columns``, in time order, and ``resolution`` says how they were cut down, so the
AI client always knows what it is looking at. Each cut also gives a note.

* Heart rate and SpO2 (``series_block``) come as raw samples while they fit, else as
  buckets of 5, 15, 30, 60 or 180 minutes (the smallest that fits), each with its
  minimum, average, maximum and count. Buckets are wall-clock slots of the local day
  (``[00:00, 00:15)``, ``[00:15, 00:30)``, ...), restarting at every local midnight;
  on a DST day the slot holding the change is longer or shorter in real time, and a
  repeated hour falls into one slot.
* Workout samples (``samples_block``) are picked, never averaged: one recorded second
  per slot of 1, 5, 10, 15, 30, 60 or 120 seconds (then 180, 240, ... seconds), the
  first of each slot. An average of cumulative distance or steps means nothing.
* A GPS track (``track_block``) keeps every nth point.

An interval the caller chose that gives more than 500 rows is a ``ToolFailure``: the
range is never cut short in silence.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import datetime, time, tzinfo
from typing import Any, TypeVar

from mifit.mcp_server.errors import ToolFailure
from mifit.mcp_server.present import iso_time
from mifit.models.workout_detail import TrackPoint, WorkoutSample

__all__ = [
    "AUTO_BUCKET_MIN",
    "AUTO_STEP_S",
    "MAX_ROWS",
    "bucket_rows",
    "choose_bucket_min",
    "choose_step_s",
    "pick",
    "samples_block",
    "series_block",
    "slot_start",
    "track_block",
]

MAX_ROWS = 500
"""The most rows (or GPS points) one block holds."""

AUTO_BUCKET_MIN = (5, 15, 30, 60, 180)
"""The bucket sizes, in minutes, a series is cut down to when it has too many samples."""

AUTO_STEP_S = (1, 5, 10, 15, 30, 60, 120)
"""The steps, in seconds, workout samples are picked at; larger steps go up by 60 s."""

_SAMPLE_COLUMNS = [
    "time",
    "heart_rate_bpm",
    "distance_m",
    "steps",
    "cadence_spm",
    "pace_s_per_km",
    "speed_mps",
]
_TRACK_COLUMNS = ["time", "latitude_deg", "longitude_deg"]

T = TypeVar("T")


def _wall(epoch_s: int, tz: tzinfo | None) -> datetime:
    if tz is None:
        return datetime.fromtimestamp(epoch_s).astimezone()
    return datetime.fromtimestamp(epoch_s, tz)


def _slot(epoch_s: int, interval_min: int, tz: tzinfo | None) -> tuple[Any, int]:
    """The local date and the slot's first minute of the day."""
    wall = _wall(epoch_s, tz)
    minute = wall.hour * 60 + wall.minute
    return wall.date(), minute - minute % interval_min


def slot_start(epoch_s: int, interval_min: int, tz: tzinfo | None) -> datetime:
    """The wall-clock start of the ``interval_min``-minute slot of the local day that
    holds ``epoch_s``, with the UTC offset in force then (``fold=0``)."""
    day, minute = _slot(epoch_s, interval_min, tz)
    naive = datetime.combine(day, time(minute // 60, minute % 60))
    if tz is None:
        return naive.astimezone()  # read as local time, under the local zone's rules
    return naive.replace(tzinfo=tz)


def _groups(
    points: Sequence[tuple[int, int]], interval_min: int, tz: tzinfo | None
) -> dict[tuple[Any, int], list[tuple[int, int]]]:
    groups: dict[tuple[Any, int], list[tuple[int, int]]] = {}
    for point in sorted(points):
        groups.setdefault(_slot(point[0], interval_min, tz), []).append(point)
    return groups


def bucket_rows(
    points: Sequence[tuple[int, int]], interval_min: int, tz: tzinfo | None
) -> list[list]:
    """``(epoch_s, value)`` points as one row per non-empty slot, in time order:
    ``[start, min, avg, max, count]``, the average rounded to one decimal."""
    rows = []
    for members in _groups(points, interval_min, tz).values():
        values = [value for _, value in members]
        start = slot_start(members[0][0], interval_min, tz)
        rows.append(
            [
                start.isoformat(timespec="seconds"),
                min(values),
                round(sum(values) / len(values), 1),
                max(values),
                len(values),
            ]
        )
    return rows


def choose_bucket_min(points: Sequence[tuple[int, int]], tz: tzinfo | None) -> int:
    """``0`` (raw samples) when the points fit in ``MAX_ROWS`` rows, else the smallest
    of ``AUTO_BUCKET_MIN`` whose buckets fit (the largest when none does)."""
    if len(points) <= MAX_ROWS:
        return 0
    for interval in AUTO_BUCKET_MIN:
        if len(_groups(points, interval, tz)) <= MAX_ROWS:
            return interval
    return AUTO_BUCKET_MIN[-1]


def pick(samples: Sequence[T], step_s: int, *, at: Callable[[T], int]) -> list[T]:
    """The first of ``samples`` (in time order) in each ``step_s``-second slot, the
    slots counted from the first sample's time ``at(samples[0])``."""
    kept: list[T] = []
    if not samples:
        return kept
    first = at(samples[0])
    last_slot = None
    for sample in samples:
        slot = (at(sample) - first) // step_s
        if slot != last_slot:
            kept.append(sample)
            last_slot = slot
    return kept


def choose_step_s(samples: Sequence[T], *, at: Callable[[T], int]) -> int:
    """The smallest step of ``AUTO_STEP_S``, then of 180, 240, 300, ... seconds, at
    which ``pick`` keeps at most ``MAX_ROWS`` samples."""
    for step in AUTO_STEP_S:
        if len(pick(samples, step, at=at)) <= MAX_ROWS:
            return step
    step = AUTO_STEP_S[-1] + 60
    while len(pick(samples, step, at=at)) > MAX_ROWS:
        step += 60
    return step


def series_block(
    points: Sequence[tuple[int, int]],
    *,
    value: str,
    what: str,
    interval_min: int | None,
    tz: tzinfo | None,
) -> tuple[dict, list[str]]:
    """``(epoch_s, value)`` samples as a block: raw, or in buckets (``interval_min``
    minutes, or chosen by ``choose_bucket_min`` when ``None``; ``0`` is raw).

    ``value`` names the value column (``"bpm"``); ``what`` names the series in the
    note (``"heart rate"``). Raises ``ToolFailure`` when the chosen resolution gives
    more than ``MAX_ROWS`` rows.
    """
    points = sorted(points)
    count = len(points)
    interval = choose_bucket_min(points, tz) if interval_min is None else interval_min
    if interval == 0:
        if count > MAX_ROWS:
            raise ToolFailure(
                f"{count} raw samples are more than the {MAX_ROWS}-row limit; omit "
                "interval_min or ask for a shorter range."
            )
        rows = [[iso_time(epoch_s, tz=tz), sample] for epoch_s, sample in points]
        return {"resolution": "raw samples", "columns": ["time", value], "rows": rows}, []
    rows = bucket_rows(points, interval, tz)
    if len(rows) > MAX_ROWS:
        raise ToolFailure(
            f"interval_min={interval} gives {len(rows)} rows, more than the "
            f"{MAX_ROWS}-row limit; pass a larger interval_min or ask for a shorter "
            "range."
        )
    block = {
        "resolution": f"{interval} min buckets",
        "columns": ["start", f"min_{value}", f"avg_{value}", f"max_{value}", "count"],
        "rows": rows,
    }
    name = what[:1].upper() + what[1:]
    note = (
        f"{name} shown as {interval} min buckets ({count} samples); ask for a shorter "
        "range or a smaller interval_min for more detail."
    )
    return block, [note]


def samples_block(
    samples: Sequence[WorkoutSample],
    *,
    interval_s: int | None,
    offset_s: int | None,
    tz: tzinfo | None,
) -> tuple[dict, list[str]]:
    """A workout's per-second samples as a block, one picked every ``interval_s``
    seconds (chosen by ``choose_step_s`` when ``None``). Times are shown in the
    workout's ``offset_s``. Raises ``ToolFailure`` when an ``interval_s`` the caller
    chose gives more than ``MAX_ROWS`` rows."""

    def at(sample: WorkoutSample) -> int:
        return sample.epoch_s

    step = choose_step_s(samples, at=at) if interval_s is None else interval_s
    kept = pick(samples, step, at=at)
    if len(kept) > MAX_ROWS:
        raise ToolFailure(
            f"interval_s={step} gives {len(kept)} rows, more than the {MAX_ROWS}-row "
            "limit; pass a larger interval_s or omit it."
        )
    rows = [
        [
            iso_time(s.epoch_s, offset_s=offset_s, tz=tz),
            s.heart_rate_bpm,
            s.distance_m,
            s.steps,
            s.cadence_spm,
            s.pace_s_per_km,
            s.speed_mps,
        ]
        for s in kept
    ]
    resolution = "every second" if step == 1 else f"every {step} s"
    block = {"resolution": resolution, "columns": list(_SAMPLE_COLUMNS), "rows": rows}
    notes = []
    if step > 1:
        notes.append(
            f"Workout samples shown every {step} s ({len(kept)} of {len(samples)} "
            "recorded seconds); pass a smaller interval_s for more detail."
        )
    return block, notes


def track_block(
    points: Sequence[TrackPoint], *, offset_s: int | None, tz: tzinfo | None
) -> tuple[dict, list[str]]:
    """A GPS track as a block of at most ``MAX_ROWS`` points: every nth fix, n the
    smallest that fits. Times are shown in the workout's ``offset_s``."""
    every = max(1, math.ceil(len(points) / MAX_ROWS))
    kept = list(points[::every])
    rows = [
        [iso_time(p.epoch_s, offset_s=offset_s, tz=tz), p.latitude_deg, p.longitude_deg]
        for p in kept
    ]
    resolution = "every point" if every == 1 else f"every {every}th point"
    block = {"resolution": resolution, "columns": list(_TRACK_COLUMNS), "rows": rows}
    notes = []
    if every > 1:
        notes.append(
            f"GPS track shown as every {every}th point ({len(kept)} of {len(points)})."
        )
    return block, notes
