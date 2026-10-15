"""Provider-neutral workout model.

Field names describe the workout, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Workout:
    """One recorded workout.

    ``start_epoch_s`` and ``end_epoch_s`` are true Unix seconds. ``utc_offset_s`` is
    the offset of the zone the workout was recorded in, for showing local clock
    times.

    ``sport`` is a neutral name in Health Connect's exercise-type vocabulary, lower
    snake case, or ``None`` when the source type has no mapping yet.
    ``source_sport_code`` is the source's own sport-type number, kept
    uninterpreted (like ``HeartRatePoint.kind``).

    ``duration_s`` is the recorded workout time and can be shorter than
    ``end_epoch_s - start_epoch_s``, because paused time is not counted.
    ``distance_m`` is in metres. ``active_kcal`` is the energy burned by the
    activity, without resting energy; ``total_kcal`` adds the resting energy burned
    during the workout. The app shows both, as active and total calories.
    ``avg_hr_bpm`` and ``max_hr_bpm`` are beats per minute.

    Any figure is ``None`` when the source did not report it, never a made-up zero.
    """

    start_epoch_s: int
    end_epoch_s: int | None
    utc_offset_s: int | None
    sport: str | None
    source_sport_code: int | None
    duration_s: int | None
    distance_m: int | None
    active_kcal: int | None
    total_kcal: int | None
    avg_hr_bpm: int | None
    max_hr_bpm: int | None
    steps: int | None
