"""Provider-neutral workout detail models: per-second samples and a GPS track.

Field names describe the workout, not any service's file format; mapping a
service's files onto these records is the job of the domain that fetches them.
"""

from __future__ import annotations

from dataclasses import dataclass

from mifit.models.workouts import Workout


@dataclass
class WorkoutSample:
    """One recorded second of a workout.

    epoch_s is true Unix seconds and the sample covers [epoch_s, epoch_s + 1).
    Paused time has no samples, so a gap between consecutive epoch_s values is a pause.
    distance_m and steps are cumulative from the start of the workout (summed from the
    source's per-second increments across all segments). Any figure the source did not
    report, or marked invalid, is None, never a made-up zero.

    A cadence_spm of 0 is a real reading: the app's average cadence counts those
    seconds. A speed_mps of 0.0 is a real standstill: the app charts it as 0.
    """

    epoch_s: int
    heart_rate_bpm: int | None
    distance_m: float | None  # cumulative, metres
    steps: int | None  # cumulative; None for sports without steps
    cadence_spm: int | None  # steps per minute (running)
    pace_s_per_km: int | None  # seconds per km (running)
    speed_mps: float | None  # metres per second (cycling; source 0.1 km/h)


@dataclass
class TrackPoint:
    """One GPS fix: true Unix seconds and WGS-84-style degrees as the device recorded them."""

    epoch_s: int
    latitude_deg: float
    longitude_deg: float


@dataclass
class WorkoutDetail:
    """One workout with its per-second samples and its GPS track.

    ``workout`` is the same summary ``workouts`` gives for this workout.

    ``samples`` and ``track`` are ``None`` when the file was not read (the result's
    notes say why; a track the caller did not ask for has no note), and ``[]`` when
    the file was read but held no points.

    The SDK writes no GPX, TCX or FIT file; these records are the building blocks for
    one.
    """

    workout: Workout
    samples: list[WorkoutSample] | None
    track: list[TrackPoint] | None
