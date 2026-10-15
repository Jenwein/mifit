"""Daily activity domain tests. ``PagedFake`` serves synthetic rows per key; nothing
here touches the network and no real health data is used.

The domain sends three daily-report reads, one per key (``valid_stand``,
``intensity``, ``calories``), all on the same endpoint, so the fake routes each
read by its key (``by_key``). Each row is the OUTER object
``{sid, tag, key, time, value, update_time, watermark}``: ``time`` is the UTC
midnight of the local date the row stands for (1760400000 = 2025-10-14, in any
zone; notes/06) and ``value`` is a JSON string holding that key's figure.
"""

import dataclasses
from datetime import timedelta, timezone

from mifit.domains.activity_daily import fetch_activity_daily
from mifit.domains.base import REGISTRY, stalled_note
from mifit.models.activity import ActivityDailySummary
from support import PagedFake, payload_with_rows_from

TEMPLATE = {
    "sid": "synthetic",
    "tag": "daily_report",
    "key": "synthetic",
    "value": "{}",
    "update_time": 0,
    "watermark": 0,
}
payload_with_rows = payload_with_rows_from(TEMPLATE)

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
ENDPOINT = "data/get_aggregated_fitness_data_by_time"
UTC = timezone.utc
CST = timezone(timedelta(hours=8))


def fake(stand=(), intensity=(), calories=(), **kw):
    """A ``PagedFake`` serving each key its own ``(outer_time, value)`` rows."""
    return PagedFake(
        by_key={
            "valid_stand": payload_with_rows(*stand),
            "intensity": payload_with_rows(*intensity),
            "calories": payload_with_rows(*calories),
        },
        **kw,
    )


def S(date, epoch_s, count, minutes, kcal):
    return ActivityDailySummary(date, epoch_s, count, minutes, kcal)


def one_day_per_key(**kw):
    """One DAY_1 row per key: count 9, 45 minutes, 320 kcal."""
    return fake(
        stand=[(DAY_1, {"count": 9})],
        intensity=[(DAY_1, {"duration": 45})],
        calories=[(DAY_1, {"calories": 320})],
        **kw,
    )


def test_activity_daily_domain_is_registered():
    assert REGISTRY["activity_daily"] is fetch_activity_daily


def test_requests_the_three_keys_for_the_window():
    transport = fake()

    fetch_activity_daily(transport, START, END, tz=UTC)

    assert transport.calls == [
        {
            "endpoint_path": ENDPOINT,
            "data_obj": {
                "tag": "daily_report",
                "key": key,
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
        for key in ("valid_stand", "intensity", "calories")
    ]


def test_merges_the_three_keys_by_day():
    transport = fake(
        stand=[(DAY_2, {"count": 7}), (DAY_1, {"count": 9})],
        intensity=[(DAY_2, {"duration": 30}), (DAY_1, {"duration": 45})],
        calories=[(DAY_2, {"calories": 250}), (DAY_1, {"calories": 320})],
    )

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    assert result.records == [
        S("2025-10-15", DAY_2, 7, 30, 250),
        S("2025-10-14", DAY_1, 9, 45, 320),
    ]
    assert result.notes == []


def test_day_in_one_key_only_gives_a_record_with_the_others_none():
    transport = fake(
        stand=[(DAY_1, {"count": 9})],
        intensity=[(DAY_2, {"duration": 30})],
    )

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    # Newest first, although the first read (valid_stand) gave DAY_1.
    assert result.records == [
        S("2025-10-15", DAY_2, None, 30, None),
        S("2025-10-14", DAY_1, 9, None, None),
    ]
    assert result.notes == []


def test_epoch_is_the_local_midnight_of_the_report_date():
    # The rows keyed 2025-10-14 (UTC midnight) stand for local 2025-10-14, which
    # starts at 2025-10-13T16:00Z at UTC+8.
    start = DAY_1 - 28800
    end = DAY_2 - 28800 + 86399

    result = fetch_activity_daily(one_day_per_key(), start, end, tz=CST)

    assert result.records == [S("2025-10-14", DAY_1 - 28800, 9, 45, 320)]


def test_zero_is_a_real_value():
    transport = fake(
        stand=[(DAY_1, {"count": 0})],
        intensity=[(DAY_1, {"duration": 0})],
        calories=[(DAY_1, {"calories": 0})],
    )

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    assert result.records == [S("2025-10-14", DAY_1, 0, 0, 0)]


def test_missing_negative_or_non_numeric_figures_become_none():
    values = [{}, {"count": None}, {"count": -1}, {"count": "9"}, {"count": True}]
    transport = fake(stand=[(DAY_1 + k * 86400, v) for k, v in enumerate(values)])

    result = fetch_activity_daily(transport, START, DAY_1 + 4 * 86400 + 86399, tz=UTC)

    assert len(result.records) == 5
    assert all(record.activity_count is None for record in result.records)
    assert result.notes == []


def test_float_figures_are_truncated():
    transport = fake(intensity=[(DAY_1, {"duration": 45.9})])

    (record,) = fetch_activity_daily(transport, START, END, tz=UTC).records

    assert record.activity_min == 45
    assert type(record.activity_min) is int


def test_raw_holds_each_keys_values_in_key_order():
    stand = [{"count": 7}, {"count": 9}]
    intensity = [{"duration": 30, "process": [1]}, {"duration": 45}]
    calories = [{"calories": 250, "goal": 500}, {"calories": 320}]
    transport = fake(
        stand=list(zip((DAY_2, DAY_1), stand)),
        intensity=list(zip((DAY_2, DAY_1), intensity)),
        calories=list(zip((DAY_2, DAY_1), calories)),
    )

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    # One section per key, in key order, each in the service's order; the values
    # untouched, ``goal`` and ``process`` included.
    assert result.raw == stand + intensity + calories


def test_notes_name_the_figure_and_follow_key_order():
    transport = fake(
        stand=[("x", {"count": 9})],
        intensity=[(DAY_1, "not json")],
        calories=[(DAY_1, "[1]")],
    )

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    assert result.notes == [
        "Skipped a daily activity-count row without a usable day timestamp.",
        "Skipped a daily activity-time row whose value is missing or not JSON.",
        "Skipped a daily active-calorie row whose value is not an object.",
    ]
    assert result.records == []
    assert result.raw == [{"count": 9}]


def test_second_row_of_a_key_for_the_same_day_is_ignored():
    transport = fake(stand=[(DAY_1, {"count": 9}), (DAY_1, {"count": 3})])

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    (record,) = result.records
    assert record.activity_count == 9
    assert result.raw == [{"count": 9}, {"count": 3}]


def test_unverified_fields_stay_out_of_the_summary():
    assert {f.name for f in dataclasses.fields(ActivityDailySummary)} == {
        "date",
        "epoch_s",
        "activity_count",
        "activity_min",
        "active_kcal",
    }


def test_empty_window_yields_no_records_and_no_notes():
    transport = fake()

    result = fetch_activity_daily(transport, START, END, tz=UTC)

    assert len(transport.calls) == 3
    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_stalled_reads_say_so_per_key():
    result = fetch_activity_daily(one_day_per_key(stalled=True), START, END, tz=UTC)

    assert result.notes == [
        stalled_note("daily activity-count rows"),
        stalled_note("daily activity-time rows"),
        stalled_note("daily active-calorie rows"),
    ]
    assert result.records == [S("2025-10-14", DAY_1, 9, 45, 320)]
