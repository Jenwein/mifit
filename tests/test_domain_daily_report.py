"""Shared daily-report reader tests. ``PagedFake`` serves synthetic rows; nothing
here touches the network and no real health data is used.

A row is the OUTER object ``{time, value}``: ``time`` is the UTC midnight of the
local date the row stands for (1760400000 = 2025-10-14, in any zone; notes/06) and
``value`` is the JSON string the wire carries.
"""

import json
from datetime import timedelta, timezone

import pytest

from mifit.domains import daily_report, passthrough
from mifit.domains.base import stalled_note
from mifit.domains.daily_report import fetch_daily_summaries, read_daily_report
from mifit.transport import PagedResult
from support import PagedFake

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
UTC = timezone.utc
CST = timezone(timedelta(hours=8))


def row(time, value):
    """A wire row; a ``str`` value is sent as is, anything else JSON-encoded."""
    return {
        "time": time,
        "value": value if isinstance(value, str) else json.dumps(value),
    }


def served(*rows, stalled=False):
    return PagedFake({"code": 0, "result": {"data_list": list(rows)}}, stalled=stalled)


def as_tuple(day, epoch_s, value):
    return (day, epoch_s, value)


def test_read_daily_report_asks_for_the_local_days():
    t = served()

    paged = read_daily_report(t, "steps", DAY_1, DAY_2 + 86399, tz=UTC)

    assert t.calls == [
        {
            "endpoint_path": "data/get_aggregated_fitness_data_by_time",
            "data_obj": {
                "tag": "daily_report",
                "key": "steps",
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
    assert isinstance(paged, PagedResult)
    assert paged.items == []


def test_read_daily_report_sends_the_tag():
    t = served()

    read_daily_report(t, "goal", START, END, tz=UTC, tag="daily_fitness")

    assert t.calls[0]["data_obj"]["tag"] == "daily_fitness"
    assert passthrough.AGGREGATED_ENDPOINT == daily_report.AGGREGATED_ENDPOINT


def test_summaries_give_one_record_per_row():
    t = served(row(DAY_2, {"a": 2}), row(DAY_1, {"a": 1}))

    result = fetch_daily_summaries(
        t,
        "k",
        DAY_1 - 28800,
        DAY_2 - 28800 + 86399,
        what="daily thing",
        summarize=as_tuple,
        tz=CST,
    )

    # Each day's epoch is its local midnight: 2025-10-14 starts at
    # 2025-10-13T16:00Z at UTC+8.
    assert result.records == [
        ("2025-10-15", DAY_2 - 28800, {"a": 2}),
        ("2025-10-14", DAY_1 - 28800, {"a": 1}),
    ]
    assert result.raw == [{"a": 2}, {"a": 1}]
    assert result.notes == []
    assert t.calls[0]["data_obj"]["key"] == "k"


def test_summaries_word_their_notes_with_what():
    t = served(
        row(DAY_1, "not json"),
        row(DAY_1, "[1]"),
        row("x", {"a": 1}),
        stalled=True,
    )

    result = fetch_daily_summaries(
        t, "k", START, END, what="daily thing", summarize=as_tuple, tz=UTC
    )

    assert result.notes == [
        "Skipped a daily thing row whose value is missing or not JSON.",
        "Skipped a daily thing row whose value is not an object.",
        "Skipped a daily thing row without a usable day timestamp.",
        stalled_note("daily thing rows"),
    ]
    assert result.records == []
    # Only the row whose value decoded is kept, although it gave no record.
    assert result.raw == [{"a": 1}]


@pytest.mark.parametrize("time", [10**20, -(10**20)], ids=["far-future", "far-past"])
def test_summaries_skip_a_day_key_out_of_range(time):
    t = served(row(time, {"a": 1}))

    result = fetch_daily_summaries(
        t, "k", START, END, what="daily thing", summarize=as_tuple, tz=UTC
    )

    assert result.records == []
    assert result.raw == [{"a": 1}]
    assert result.notes == ["Skipped a daily thing row without a usable day timestamp."]
