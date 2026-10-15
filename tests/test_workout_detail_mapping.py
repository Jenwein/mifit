"""Tests for the workout-detail mapping: decoded FDS files (the A layer) to the
neutral ``WorkoutSample`` / ``TrackPoint`` records (the B layer).

Inputs are hand-written A dicts in the decoder's shape (spec §9): stored, unscaled
values under the ``schemas.zip`` names. Every value is synthetic.
"""

import dataclasses
import math
import time

import pytest

from mifit.domains.workout_detail import to_samples, to_track
from mifit.models.workout_detail import TrackPoint, WorkoutDetail, WorkoutSample

T = 1700000000
RUN = 22
RIDE = 23
INITIAL_ALTITUDE = 50.0  # stays in the decoded input; altitude is not mapped to B


def seg(resume, points):
    """A decoded record segment starting at ``resume`` and holding ``points``."""
    return {
        "initialAltitude": INITIAL_ALTITUDE,
        "recordCount": len(points),
        "resumeTimestamp": resume,
        "pauseType": 1,
        "segmentType": 0,
        "dataPoints": list(points),
    }


def record(*segments):
    """A decoded record file holding ``segments``."""
    return {"header": {}, "segments": list(segments)}


def gps(*points):
    """A decoded GPS file holding ``points``."""
    return {"header": {}, "gpsPoints": list(points)}


def fix(epoch_s, latitude, longitude):
    """A decoded GPS point; the file stores longitude before latitude."""
    return {"time": epoch_s, "longitude": longitude, "latitude": latitude}


def column(samples, name):
    return [getattr(s, name) for s in samples]


def test_sample_time_is_segment_resume_plus_index():
    samples = to_samples(record(seg(T, [{}, {}]), seg(T + 100, [{}])), RUN)

    assert column(samples, "epoch_s") == [T, T + 1, T + 100]


def test_run_distance_is_cumulative_metres_from_decimetres():
    points = [{"newDistance": 25}, {"newDistance": 30}]
    later = [{"newDistance": 0}, {"newDistance": 10}]

    samples = to_samples(record(seg(T, points), seg(T + 100, later)), RUN)

    assert column(samples, "distance_m") == [2.5, 5.5, 5.5, 6.5]


def test_ride_distance_is_cumulative_metres():
    samples = to_samples(record(seg(T, [{"newDistance": 5}, {"newDistance": 6}])), RIDE)

    assert column(samples, "distance_m") == [5.0, 11.0]
    assert all(type(d) is float for d in column(samples, "distance_m"))


def test_distance_without_a_value_is_none_and_not_accumulated():
    points = [{"newDistance": 25}, {}, {"newDistance": 30}]

    samples = to_samples(record(seg(T, points)), RUN)

    assert column(samples, "distance_m") == [2.5, None, 5.5]


def test_steps_are_cumulative_and_run_only():
    points = [{"newSteps": 3}, {}, {"newSteps": 2}]

    run = to_samples(record(seg(T, points)), RUN)
    ride = to_samples(record(seg(T, points)), RIDE)

    assert column(run, "steps") == [3, None, 5]
    assert column(ride, "steps") == [None, None, None]


def test_heart_rate_zero_becomes_none():
    points = [{"heartRate": 120}, {"heartRate": 0}, {}]

    for proto in (RUN, RIDE):
        samples = to_samples(record(seg(T, points)), proto)
        assert column(samples, "heart_rate_bpm") == [120, None, None]


def test_cadence_zero_is_kept_pace_zero_becomes_none_both_run_only():
    points = [{"cadence": 170, "pace": 360}, {"cadence": 0, "pace": 0}, {}]

    run = to_samples(record(seg(T, points)), RUN)
    ride = to_samples(record(seg(T, points)), RIDE)

    # A cadence of 0 is a real reading (the app's average counts those seconds);
    # absent is no reading.
    assert column(run, "cadence_spm") == [170, 0, None]
    assert column(run, "pace_s_per_km") == [360, None, None]
    # The ride's cadence was invalid in every checked file (D20): not mapped.
    assert column(ride, "cadence_spm") == [None, None, None]
    assert column(ride, "pace_s_per_km") == [None, None, None]


def test_speed_converts_tenth_kmh_and_keeps_zero():
    points = [{"speed": 360}, {"speed": 0}, {}]

    ride = to_samples(record(seg(T, points)), RIDE)
    run = to_samples(record(seg(T, points)), RUN)

    # 0.1 km/h -> m/s; 0 is a real standstill, absent is no reading.
    assert column(ride, "speed_mps") == [10.0, 0.0, None]
    assert type(ride[1].speed_mps) is float
    assert column(run, "speed_mps") == [None, None, None]


def test_absent_field_gives_none_for_every_sample():
    points = [{"heartRate": 120, "newSteps": 3}, {"heartRate": 121, "newSteps": 2}]

    samples = to_samples(record(seg(T, points), seg(T + 100, points)), RUN)

    assert all(s.distance_m is None for s in samples)
    assert column(samples, "steps") == [3, 5, 8, 10]


def test_whole_samples_of_a_run_and_a_ride():
    run_point = {
        "newCalories": 1,
        "newSteps": 3,
        "heartRate": 150,
        "isKilometer": 0,
        "isAltitudeRising": 1,
        "altitudeChange": 4,
        "newDistance": 25,
        "stride": 80,
        "cadence": 172,
        "pace": 355,
    }
    ride_point = {
        "newCalories": 2,
        "heartRate": 130,
        "isKilometer": 0,
        "isAltitudeRising": 0,
        "altitudeChange": 2,
        "newDistance": 6,
        "speed": 216,
    }

    run = to_samples(record(seg(T, [run_point])), RUN)
    ride = to_samples(record(seg(T, [ride_point])), RIDE)

    assert run == [WorkoutSample(T, 150, 2.5, 3, 172, 355, None)]
    assert ride == [WorkoutSample(T, 130, 6.0, None, None, None, 6.0)]


def test_track_maps_latitude_and_longitude_in_order():
    track, dropped = to_track(gps({"time": T, "longitude": 20.5, "latitude": 10.5}))

    assert track == [TrackPoint(T, 10.5, 20.5)]
    assert dropped == 0


def test_track_keeps_float32_values_unrounded():
    latitude, longitude = 10.000100135803223, 20.000099182128906

    track, _ = to_track(gps(fix(T, latitude, longitude)))

    assert track[0].latitude_deg == latitude
    assert track[0].longitude_deg == longitude


def test_track_drops_unusable_positions_and_counts_them():
    kept = [
        fix(T, 10.5, 20.5),
        fix(T + 6, 90.0, -180.0),  # on the bounds
        fix(T + 7, 0.0, 20.5),  # only (0, 0) itself is dropped
    ]
    bad = [
        fix(T + 1, 0.0, 0.0),
        fix(T + 2, 91.0, 20.5),
        fix(T + 3, 10.5, -181.0),
        fix(T + 4, math.nan, 20.5),
        fix(T + 5, 10.5, math.inf),
    ]

    track, dropped = to_track(gps(kept[0], *bad, *kept[1:]))

    assert dropped == 5
    assert track == [TrackPoint(p["time"], p["latitude"], p["longitude"]) for p in kept]


@pytest.mark.parametrize("missing", ["time", "longitude", "latitude"])
def test_track_is_none_when_positions_are_marked_invalid(missing):
    unmarked = fix(T + 1, 10.5, 20.5)
    del unmarked[missing]

    assert to_track(gps(fix(T, 10.5, 20.5), fix(T + 1, 0.0, 0.0), unmarked)) == (None, 0)


def test_empty_files_give_empty_lists():
    assert to_samples({"header": {}, "segments": []}, RUN) == []
    assert to_samples(record(seg(T, [])), RIDE) == []
    assert to_track({"header": {}, "gpsPoints": []}) == ([], 0)


@pytest.fixture
def machine_zone(monkeypatch):
    """Sets the process time zone; the original one is back after the test."""

    def set_zone(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_zone
    monkeypatch.undo()
    time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
@pytest.mark.parametrize("zone", ["UTC", "America/New_York"])
def test_epoch_s_ignores_the_machine_zone(machine_zone, zone):
    machine_zone(zone)

    samples = to_samples(record(seg(T, [{}, {}]), seg(T + 100, [{}])), RUN)
    track, _ = to_track(gps(fix(T + 3, 10.5, 20.5)))

    assert column(samples, "epoch_s") == [T, T + 1, T + 100]
    assert track == [TrackPoint(T + 3, 10.5, 20.5)]


def test_b_models_hold_only_verified_fields():
    def names(model):
        return [f.name for f in dataclasses.fields(model)]

    assert names(WorkoutSample) == [
        "epoch_s",
        "heart_rate_bpm",
        "distance_m",
        "steps",
        "cadence_spm",
        "pace_s_per_km",
        "speed_mps",
    ]
    assert names(TrackPoint) == ["epoch_s", "latitude_deg", "longitude_deg"]
    assert names(WorkoutDetail) == ["workout", "samples", "track"]
