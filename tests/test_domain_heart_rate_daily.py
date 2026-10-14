"""Daily heart-rate summary domain tests. ``PagedFake`` serves a synthetic payload;
nothing here touches the network and no real health data is used.

The fixture follows the live-verified shape of the decrypted envelope: rows live
under ``result.data_list`` (with ``has_more``/``next_key`` beside them). Each row
is the OUTER object ``{sid, tag, key, time, value, update_time, watermark}``:
``time`` is the UTC midnight of the local date the row stands for (1760400000 =
2025-10-14, in any zone; notes/06) and ``value`` is a JSON string holding the
day's figures (``avg_hr``, ``max_hr``, ``min_hr``, ``avg_rhr``, the zone
durations, ``abnormal_hr_count`` and ``latest_hr``). The second fixture row has
no ``avg_rhr``.
"""

import dataclasses
import json
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from mifit.domains.base import REGISTRY, stalled_note
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.models.heart_rate import HeartRateDailySummary
from support import ABSENT, PagedFake, payload_with_rows_from

FIXTURE = Path(__file__).parent / "fixtures" / "hr_daily_sample.json"

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
ENDPOINT = "data/get_aggregated_fitness_data_by_time"
UTC = timezone.utc
CST = timezone(timedelta(hours=8))


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


payload_with_rows = payload_with_rows_from(load_fixture()["result"]["data_list"][0])


def fixture_values():
    return [json.loads(row["value"]) for row in load_fixture()["result"]["data_list"]]


def figures(**overrides):
    """A decoded daily value with only the four mapped figures, then overrides."""
    value = {"avg_hr": 70, "max_hr": 130, "min_hr": 50, "avg_rhr": 60}
    value.update(overrides)
    return value


def test_heart_rate_daily_domain_is_registered():
    assert REGISTRY["heart_rate_daily"] is fetch_heart_rate_daily


def test_requests_daily_report_heart_rate_for_the_window():
    transport = PagedFake(load_fixture())

    fetch_heart_rate_daily(transport, START, END, tz=UTC)

    assert transport.calls == [
        {
            "endpoint_path": ENDPOINT,
            "data_obj": {
                "tag": "daily_report",
                "key": "heart_rate",
                "reverse": True,
                "limit": 50,
                # (start, end] over the daily-report keys: the key of the day
                # before 2025-10-14 through the key of 2025-10-15.
                "start_time": DAY_1 - 86400,
                "end_time": DAY_2,
            },
            "list_key": "data_list",
            "max_pages": None,
        }
    ]


def test_maps_day_figures():
    result = fetch_heart_rate_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    assert result.records[0] == HeartRateDailySummary(
        date="2025-10-14",
        epoch_s=DAY_1,
        avg_bpm=70,
        max_bpm=130,
        min_bpm=50,
        resting_bpm=60,
    )
    assert result.notes == []


def test_second_day_without_resting_figure_keeps_the_rest():
    result = fetch_heart_rate_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    assert result.records[1] == HeartRateDailySummary(
        date="2025-10-15",
        epoch_s=DAY_2,
        avg_bpm=72,
        max_bpm=140,
        min_bpm=52,
        resting_bpm=None,
    )


def test_epoch_is_the_local_midnight_of_the_report_date():
    # The row keyed 2025-10-14 (UTC midnight) stands for local 2025-10-14, which
    # starts at 2025-10-13T16:00Z at UTC+8.
    start = DAY_1 - 28800
    end = DAY_2 - 28800 + 86399

    result = fetch_heart_rate_daily(PagedFake(load_fixture()), start, end, tz=CST)

    assert result.records[0].date == "2025-10-14"
    assert result.records[0].epoch_s == DAY_1 - 28800


def test_missing_or_zero_figures_become_none():
    payload = payload_with_rows(
        (DAY_1, {"avg_hr": 70, "max_hr": 130, "min_hr": 50}),  # avg_rhr absent
        (DAY_1 + 86400, figures(avg_rhr=None)),
        (DAY_1 + 2 * 86400, figures(avg_rhr=0)),
        (DAY_1 + 3 * 86400, figures(max_hr=0)),
    )

    result = fetch_heart_rate_daily(
        PagedFake(payload), START, DAY_1 + 3 * 86400, tz=UTC
    )

    assert [
        (r.avg_bpm, r.max_bpm, r.min_bpm, r.resting_bpm) for r in result.records
    ] == [
        (70, 130, 50, None),
        (70, 130, 50, None),
        (70, 130, 50, None),
        (70, None, 50, 60),
    ]
    assert result.notes == []


def test_non_numeric_figures_become_none():
    payload = payload_with_rows(
        (DAY_1, figures(avg_hr="72")),
        (DAY_2, figures(avg_hr=True, max_hr=[70], min_hr={"bpm": 50}, avg_rhr="x")),
    )

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert [
        (r.avg_bpm, r.max_bpm, r.min_bpm, r.resting_bpm) for r in result.records
    ] == [
        (None, 130, 50, 60),
        (None, None, None, None),
    ]


def test_negative_figures_become_none():
    # A bpm at or below zero is not a measurement.
    payload = payload_with_rows(
        (DAY_1, figures(avg_hr=-70, max_hr=-1, min_hr=-0.5, avg_rhr=-60)),
    )

    (record,) = fetch_heart_rate_daily(
        PagedFake(payload), START, END, tz=UTC
    ).records

    assert (record.avg_bpm, record.max_bpm, record.min_bpm, record.resting_bpm) == (
        None,
        None,
        None,
        None,
    )


def test_float_figures_are_truncated_and_non_finite_become_none():
    payload = payload_with_rows(
        (DAY_1, figures(avg_hr=70.9, max_hr=float("nan"), min_hr=float("inf"))),
    )

    (record,) = fetch_heart_rate_daily(
        PagedFake(payload), START, END, tz=UTC
    ).records

    assert (record.avg_bpm, record.max_bpm, record.min_bpm, record.resting_bpm) == (
        70,
        None,
        None,
        60,
    )


def test_row_with_every_figure_missing_still_gives_a_record():
    payload = payload_with_rows((DAY_1, {"abnormal_hr_count": 0}))

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == [
        HeartRateDailySummary(
            date="2025-10-14",
            epoch_s=DAY_1,
            avg_bpm=None,
            max_bpm=None,
            min_bpm=None,
            resting_bpm=None,
        )
    ]
    assert result.notes == []


def test_raw_keeps_decoded_values_untouched():
    result = fetch_heart_rate_daily(PagedFake(load_fixture()), START, END, tz=UTC)

    # Zones, abnormal_hr_count and latest_hr stay in raw as the service sent them.
    assert result.raw == fixture_values()
    assert "latest_hr" in result.raw[0]
    assert "fat_burning_hr_zone_duration" in result.raw[0]


@pytest.mark.parametrize(
    "bad_value",
    ["not json", "[1]", None, ABSENT],
    ids=["not-json", "not-an-object", "null", "absent"],
)
def test_row_with_bad_value_is_skipped_with_note(bad_value):
    payload = payload_with_rows((DAY_1, figures()))
    row = payload["result"]["data_list"][0]
    if bad_value is ABSENT:
        del row["value"]
    else:
        row["value"] = bad_value

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert len(result.notes) == 1
    assert result.raw == []


@pytest.mark.parametrize(
    "bad_time",
    [ABSENT, None, "x", True, str(DAY_1)],
    ids=["absent", "null", "not-a-number", "bool", "numeric-string"],
)
def test_row_without_usable_day_is_skipped_with_note(bad_time):
    payload = payload_with_rows((DAY_1, figures()))
    row = payload["result"]["data_list"][0]
    if bad_time is ABSENT:
        del row["time"]
    else:
        row["time"] = bad_time

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert len(result.notes) == 1
    # The decoded value is kept even though the row gave no record.
    assert result.raw == [figures()]


def test_unverified_fields_stay_out_of_the_summary():
    assert {f.name for f in dataclasses.fields(HeartRateDailySummary)} == {
        "date",
        "epoch_s",
        "avg_bpm",
        "max_bpm",
        "min_bpm",
        "resting_bpm",
    }


def test_empty_window_yields_no_records_and_no_notes():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_stalled_read_says_so():
    payload = payload_with_rows((None, figures()), (DAY_2, figures()))

    result = fetch_heart_rate_daily(PagedFake(payload, stalled=True), START, END, tz=UTC)

    # After the row's own note, and the rows that were read are still returned.
    assert len(result.notes) == 2
    assert result.notes[-1] == stalled_note("daily heart-rate rows")
    assert [r.date for r in result.records] == ["2025-10-15"]


def test_skip_notes_keep_their_wording():
    payload = payload_with_rows((DAY_1, "not json"), (DAY_2, "[1]"), ("x", figures()))

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.notes == [
        "Skipped a daily heart-rate row whose value is missing or not JSON.",
        "Skipped a daily heart-rate row whose value is not an object.",
        "Skipped a daily heart-rate row without a usable day timestamp.",
    ]
