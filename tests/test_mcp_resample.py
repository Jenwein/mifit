"""``mifit.mcp_server.resample``: dense data as column blocks of at most 500 rows.

Buckets are wall-clock slots of the local day; workout samples are picked, never
averaged; GPS tracks keep every nth point. Every value here is synthetic.
"""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from mifit.mcp_server import resample
from mifit.mcp_server.errors import ToolFailure
from mifit.mcp_server.present import iso_time
from mifit.models.workout_detail import TrackPoint, WorkoutSample

CST = timezone(timedelta(hours=8))
BERLIN = ZoneInfo("Europe/Berlin")

MIDNIGHT = 1791302400  # 2026-10-07 00:00 +08:00
WEEK = [(MIDNIGHT + 60 * i, 60) for i in range(7 * 1440)]  # a full week of minute samples
T0 = 1790820000  # 2026-10-01 10:00 +08:00


def _samples(count):
    return [
        WorkoutSample(T0 + i, 120 + i % 30, 2.5 * i, 3 * i, 170, 360, None)
        for i in range(count)
    ]


def test_bucket_rows_aligned_to_local_midnight():
    pts = [(MIDNIGHT, 60), (MIDNIGHT + 60, 70), (MIDNIGHT + 240, 65), (MIDNIGHT + 300, 80)]
    assert resample.bucket_rows(pts, 5, CST) == [
        ["2026-10-07T00:00:00+08:00", 60, 65.0, 70, 3],
        ["2026-10-07T00:05:00+08:00", 80, 80.0, 80, 1],
    ]


def test_bucket_rows_are_in_time_order_whatever_the_input_order():
    pts = [(MIDNIGHT + 300, 80), (MIDNIGHT, 60)]
    assert [row[0] for row in resample.bucket_rows(pts, 5, CST)] == [
        "2026-10-07T00:00:00+08:00",
        "2026-10-07T00:05:00+08:00",
    ]


def test_bucket_average_is_rounded_to_one_decimal():
    pts = [(MIDNIGHT, 60), (MIDNIGHT + 60, 61), (MIDNIGHT + 120, 61)]
    assert resample.bucket_rows(pts, 5, CST)[0][2] == 60.7


def test_buckets_across_spring_forward():
    pts = [(1774745400, 50), (1774746600, 60)]  # 01:50 +01:00, 03:10 +02:00
    assert [r[0] for r in resample.bucket_rows(pts, 60, BERLIN)] == [
        "2026-03-29T01:00:00+01:00",
        "2026-03-29T03:00:00+02:00",
    ]


def test_repeated_hour_falls_into_one_slot():
    rows = resample.bucket_rows([(1792888200, 50), (1792891800, 60)], 60, BERLIN)
    assert rows == [["2026-10-25T02:00:00+02:00", 50, 55.0, 60, 2]]


def test_auto_buckets_for_a_full_week_of_minutes():
    assert resample.choose_bucket_min(WEEK, CST) == 30  # 5: 2016, 15: 672, 30: 336
    assert resample.choose_bucket_min(WEEK[:500], CST) == 0


def test_series_block_raw_and_bucketed():
    block, notes = resample.series_block(
        [(MIDNIGHT, 61)], value="bpm", what="heart rate", interval_min=None, tz=CST
    )
    assert block == {
        "resolution": "raw samples",
        "columns": ["time", "bpm"],
        "rows": [["2026-10-07T00:00:00+08:00", 61]],
    }
    assert notes == []
    block, notes = resample.series_block(
        WEEK, value="bpm", what="heart rate", interval_min=None, tz=CST
    )
    assert block["resolution"] == "30 min buckets" and len(block["rows"]) == 336
    assert block["columns"] == ["start", "min_bpm", "avg_bpm", "max_bpm", "count"]
    assert notes == [
        "Heart rate shown as 30 min buckets (10080 samples); ask for a shorter "
        "range or a smaller interval_min for more detail."
    ]
    _, notes = resample.series_block(
        WEEK, value="spo2_pct", what="SpO2", interval_min=None, tz=CST
    )
    assert notes[0].startswith("SpO2 shown as 30 min buckets")


def test_series_block_raw_rows_are_sorted():
    block, _ = resample.series_block(
        [(MIDNIGHT + 60, 2), (MIDNIGHT, 1)], value="bpm", what="heart rate",
        interval_min=0, tz=CST,
    )
    assert [row[1] for row in block["rows"]] == [1, 2]


def test_series_block_takes_an_explicit_interval():
    block, notes = resample.series_block(
        WEEK[:1440], value="bpm", what="heart rate", interval_min=60, tz=CST
    )
    assert block["resolution"] == "60 min buckets" and len(block["rows"]) == 24
    assert notes[0].startswith("Heart rate shown as 60 min buckets (1440 samples)")


def test_series_block_refuses_an_explicit_interval_that_does_not_fit():
    with pytest.raises(ToolFailure, match="interval_min=5 gives 2016 rows"):
        resample.series_block(WEEK, value="bpm", what="heart rate", interval_min=5, tz=CST)
    with pytest.raises(ToolFailure, match="10080 raw samples"):
        resample.series_block(WEEK, value="bpm", what="heart rate", interval_min=0, tz=CST)


def test_series_block_of_nothing_is_an_empty_raw_block():
    block, notes = resample.series_block(
        [], value="bpm", what="heart rate", interval_min=None, tz=CST
    )
    assert block == {"resolution": "raw samples", "columns": ["time", "bpm"], "rows": []}
    assert notes == []


def test_pick_takes_the_first_sample_of_each_slot_across_a_pause():
    times = [T0, T0 + 1, T0 + 2, T0 + 7, T0 + 8, T0 + 9]
    assert resample.pick(times, 5, at=lambda t: t) == [T0, T0 + 7]


@pytest.mark.parametrize(("seconds", "step"), [(400, 1), (600, 5), (3600, 10), (72000, 180)])
def test_choose_step_s(seconds, step):
    assert resample.choose_step_s(list(range(seconds)), at=lambda t: t) == step


def test_samples_block_uses_the_workout_offset_and_notes_the_step():
    block, notes = resample.samples_block(
        _samples(600), interval_s=None, offset_s=28800, tz=timezone.utc
    )
    assert block["resolution"] == "every 5 s" and len(block["rows"]) == 120
    assert block["columns"] == [
        "time", "heart_rate_bpm", "distance_m", "steps", "cadence_spm", "pace_s_per_km",
        "speed_mps",
    ]
    assert block["rows"][0] == [iso_time(T0, offset_s=28800), 120, 0.0, 0, 170, 360, None]
    assert block["rows"][1][0] == iso_time(T0 + 5, offset_s=28800)
    assert notes == [
        "Workout samples shown every 5 s (120 of 600 recorded seconds); pass a smaller "
        "interval_s for more detail."
    ]


def test_samples_block_every_second_when_it_fits():
    block, notes = resample.samples_block(_samples(10), interval_s=None, offset_s=0, tz=CST)
    assert block["resolution"] == "every second" and len(block["rows"]) == 10
    assert notes == []


def test_samples_block_refuses_an_explicit_step_that_does_not_fit():
    with pytest.raises(ToolFailure, match="interval_s=1 gives 600 rows"):
        resample.samples_block(_samples(600), interval_s=1, offset_s=28800, tz=CST)


def test_track_block_thins_to_500_points():
    pts = [TrackPoint(T0 + i, 10.0, 20.0) for i in range(1201)]
    block, notes = resample.track_block(pts, offset_s=28800, tz=CST)
    assert block["resolution"] == "every 3th point" and len(block["rows"]) == 401
    assert block["columns"] == ["time", "latitude_deg", "longitude_deg"]
    assert block["rows"][1] == [iso_time(T0 + 3, offset_s=28800), 10.0, 20.0]
    assert notes == ["GPS track shown as every 3th point (401 of 1201)."]


def test_track_block_keeps_a_short_track_whole():
    block, notes = resample.track_block(
        [TrackPoint(T0, 1.0, 2.0)], offset_s=None, tz=CST
    )
    assert block == {
        "resolution": "every point",
        "columns": ["time", "latitude_deg", "longitude_deg"],
        "rows": [["2026-10-01T10:00:00+08:00", 1.0, 2.0]],
    }
    assert notes == []
