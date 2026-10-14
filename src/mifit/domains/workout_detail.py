"""Workout detail domain: a workout's decoded FDS files mapped onto neutral records.

``to_samples`` turns a decoded record file into one ``WorkoutSample`` per recorded
second, and ``to_track`` turns a decoded GPS file into ``TrackPoint``s. Their input is
the A layer of ``mifit.fds.layouts``: stored, unscaled values under the
``schemas.zip`` field names (spec §9). This module is the only place where those
names meet the neutral ones, and the only place values are scaled (spec §8).

The rules (spec §8; each one checked against the workout's summary, spec §4.9):

* ``epoch_s`` is the segment's ``resumeTimestamp`` plus the point's index in it: the
  file holds one point per second, in true Unix seconds. The app's display shift is
  never applied (D11).
* ``heart_rate_bpm`` is ``heartRate``; ``0`` is the device's "no reading", so it
  becomes ``None``.
* ``distance_m`` is the running sum of ``newDistance`` over the whole workout,
  divided by the format's distance divisor (``_DISTANCE_DIVISOR``): format 22 stores
  decimetres, format 23 metres. A point without it gives ``None`` and adds nothing.
* ``steps`` is the running sum of ``newSteps``, format 22 only; format 23 records no
  steps.
* ``cadence_spm`` (``cadence``) and ``pace_s_per_km`` (``pace``) come from format 22
  only, and ``0`` becomes ``None``.
* ``speed_mps`` is ``speed`` (0.1 km/h) divided by 36, format 23 only. ``0`` is a
  real standstill and stays ``0.0``.
* ``altitude_m`` starts each segment at its ``initialAltitude``. In each second that
  has both ``isAltitudeRising`` and ``altitudeChange``, it moves by
  ``altitudeChange / 10`` metres, up or down as ``isAltitudeRising`` says, and that
  second's altitude is the new value. A second without both has no altitude and
  leaves the running value alone. A segment whose ``initialAltitude`` is not finite
  has no altitude at all. This is the curve the app charts
  (``SportRecordBaseParser``); for the run its extremes are 2-3 m off the summary's,
  which the live check settles (spec §16 Q4).
* A track point is the GPS ``time`` with the ``latitude`` and ``longitude`` float32
  values as they are, unrounded (the file stores longitude first). When any point
  lacks one of the three, the file marks them invalid and the app drops the whole
  track, so the track is ``None`` (D23). A point whose position is not finite,
  outside ±90 / ±180, or exactly (0, 0) is left out and counted.

Running sums are kept in stored units and divided once, so no rounding error builds up
over a long workout.

These stay in ``raw`` only, because their meaning is unverified (design §2):

* per-second energy (``newCalories``): the run's sum is 0.3% off the summary;
* ``stride``, ``verticalStrideRatio`` and ``verticalAmplitude``: their averages are
  only about 1% off the summary's;
* the impact-force triple (``newImpactForce``, ``newContactTime``,
  ``newFlightTime``): not checked against anything;
* ``contactRatio``, ``equalIntensityPace`` and ``runningPower``: marked invalid in the
  checked files;
* ``workoutcount`` and the ride's ``cadence``: marked invalid in the checked files
  (the ride had no cadence sensor, D20);
* ``isKilometer``: it matches the summary, but the cumulative distance already gives
  the kilometre marks;
* the raw altitude bits: ``altitude_m`` is built from them;
* the segment fields (``segmentType``, ``pauseType`` and the segment totals): their
  meaning is unverified (spec §16 Q3); a pause already shows as a gap in ``epoch_s``;
* the GPS accuracy (``locationHorizontalAccuracy``): it looks like metres but is
  unverified;
* the GPS ``locationSpeed`` and ``gpsSource``: the speed is 0.87-0.95 of the speed
  the track gives, and its unit is unknown (D19).
"""

from __future__ import annotations

import math

from mifit.domains.base import as_float
from mifit.models.workout_detail import TrackPoint, WorkoutSample

_RUN = 22
_RIDE = 23

_DISTANCE_DIVISOR = {22: 10, 23: 1}
"""Stored ``newDistance`` units per metre, by format (the APK's
``getDistanceRecordTimes``). Its keys are the formats this module maps."""

_SPEED_UNITS_PER_MPS = 36
"""``speed`` is stored in 0.1 km/h; 1 m/s is 3.6 km/h, so 36 stored units."""

_POSITION = ("time", "longitude", "latitude")


def to_samples(record_raw: dict, proto: int) -> list[WorkoutSample]:
    """One ``WorkoutSample`` per point of a decoded record file of format ``proto``,
    in file order (see the module docstring for the rules).

    ``proto`` must be one of the formats in ``_DISTANCE_DIVISOR`` (``KeyError``
    otherwise). A file without points gives ``[]``.
    """
    divisor = _DISTANCE_DIVISOR[proto]
    run = proto == _RUN
    ride = proto == _RIDE
    samples = []
    distance = 0  # stored units since the start of the workout
    steps = 0
    for segment in record_raw["segments"]:
        start = segment["resumeTimestamp"]
        initial_altitude = as_float(segment.get("initialAltitude"))
        climb = 0  # tenths of a metre since the segment start
        for i, point in enumerate(segment["dataPoints"]):
            distance_m = None
            if "newDistance" in point:
                distance += point["newDistance"]
                distance_m = distance / divisor

            steps_now = None
            if run and "newSteps" in point:
                steps += point["newSteps"]
                steps_now = steps

            altitude_m = None
            rising = point.get("isAltitudeRising")
            change = point.get("altitudeChange")
            if initial_altitude is not None and rising is not None and change is not None:
                climb += change if rising else -change
                altitude_m = initial_altitude + climb / 10

            speed_mps = None
            if ride and "speed" in point:
                speed_mps = point["speed"] / _SPEED_UNITS_PER_MPS

            samples.append(
                WorkoutSample(
                    epoch_s=start + i,
                    heart_rate_bpm=point.get("heartRate") or None,
                    distance_m=distance_m,
                    steps=steps_now,
                    cadence_spm=(point.get("cadence") or None) if run else None,
                    pace_s_per_km=(point.get("pace") or None) if run else None,
                    speed_mps=speed_mps,
                    altitude_m=altitude_m,
                )
            )
    return samples


def to_track(gps_raw: dict) -> tuple[list[TrackPoint] | None, int]:
    """The track of a decoded GPS file and the number of points it left out.

    The track is ``None`` (and the count ``0``) when the file marks its times or
    positions invalid; ``[]`` when the file holds no points.
    """
    track = []
    dropped = 0
    for point in gps_raw["gpsPoints"]:
        if not all(name in point for name in _POSITION):
            return None, 0
        latitude = point["latitude"]
        longitude = point["longitude"]
        if _usable(latitude, longitude):
            track.append(TrackPoint(point["time"], latitude, longitude))
        else:
            dropped += 1
    return track, dropped


def _usable(latitude: float, longitude: float) -> bool:
    """Whether a fix is a usable position: finite, within ±90 / ±180, and not exactly
    (0, 0)."""
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and abs(latitude) <= 90
        and abs(longitude) <= 180
        and (latitude, longitude) != (0.0, 0.0)
    )
