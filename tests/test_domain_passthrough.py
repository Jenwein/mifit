"""Generic A-layer read tests (``fetch_raw_rows``, ``fetch_daily_rows``).
``PagedFake`` / ``WindowFake`` serve synthetic rows; nothing here touches the
network and no real health data is used.

The rows follow the live outer shape (notes/08, the domain catalog): raw rows are
``{key, sid, time, update_time, value, zone_name, zone_offset}`` and daily rows are
``{tag, key, time, value, update_time, watermark, source_sid_list, sid}``, both with
``value`` a JSON *string*. A generic read returns each row with that string decoded
and its outer fields untouched, and never a normalized record.
"""

import copy
import json
from datetime import timezone

import pytest

from mifit.domains.base import REGISTRY, DomainResult, stalled_note
from mifit.domains.passthrough import (
    AGGREGATED_ENDPOINT,
    fetch_daily_rows,
    fetch_raw_rows,
)
from mifit.domains.raw_window import RAW_ENDPOINT
from support import PagedFake, WindowFake

START = 1760371200  # 2025-10-13T16:00:00Z, a local midnight at UTC+8
END = START + 86399
DAY_1 = 1760400000  # 2025-10-14T00:00:00Z, a UTC midnight
DAY_2 = DAY_1 + 86400
UTC = timezone.utc

ROW = {
    "key": "stress",
    "sid": "synthetic",
    "time": 1760400600,
    "update_time": 1760401000,
    "value": '{"time": 1760400600, "stress": 30}',
    "zone_name": "Asia/Shanghai",
    "zone_offset": 28800,
}

DAILY_ROW = {
    "tag": "daily_report",
    "key": "valid_stand",
    "time": DAY_1,
    "value": '{"count": 9}',
    "update_time": 1760500000,
    "watermark": 1760500000,
    "source_sid_list": ["synthetic"],
    "sid": "synthetic",
}

UNDECODABLE_NOTE = (
    "Kept 1 'stress' row(s) whose value could not be decoded as JSON, "
    "as the service sent it."
)
DAILY_UNDECODABLE_NOTE = (
    "Kept 1 'valid_stand' row(s) whose value could not be decoded as JSON, "
    "as the service sent it."
)


def served(entries, **options):
    """A ``PagedFake`` whose paged read serves ``entries`` as they are."""
    return PagedFake({"result": {"data_list": entries}}, **options)


def raw_row(outer_time, value=None):
    """A synthetic stress row at ``outer_time``; ``value`` is encoded to JSON unless
    it is already the wire string."""
    if value is None:
        value = {"time": outer_time, "stress": 30}
    return {
        **ROW,
        "time": outer_time,
        "value": value if isinstance(value, str) else json.dumps(value),
    }


def daily_row(outer_time, value=None):
    if value is None:
        value = {"count": 9}
    return {
        **DAILY_ROW,
        "time": outer_time,
        "value": value if isinstance(value, str) else json.dumps(value),
    }


# fetch_raw_rows


def test_raw_rows_ask_a_day_early_and_keep_the_window():
    # Newest first, as the service sends with ``reverse``.
    rows = [raw_row(t) for t in (END + 1, END, START + 3600, START, START - 1)]
    fake = served(rows)

    result = fetch_raw_rows(fake, "stress", START, END)

    assert isinstance(result, DomainResult)
    assert [r["time"] for r in result.raw] == [END, START + 3600, START]
    assert result.records == []
    assert result.notes == []
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["endpoint_path"] == RAW_ENDPOINT
    assert call["data_obj"] == {
        "key": "stress",
        "start_time": START - 86400,
        "end_time": END,
        "reverse": True,
    }


def test_raw_rows_decode_value_and_keep_outer_fields():
    result = fetch_raw_rows(served([ROW]), "stress", START, END)

    assert result.raw[0] == {**ROW, "value": {"time": 1760400600, "stress": 30}}


def test_raw_rows_keep_non_object_json_values():
    huge = "9" * 5000  # more digits than Python parses: ``json.loads`` raises ValueError
    rows = [
        raw_row(START + 4, "[1, 2]"),
        raw_row(START + 3, '"x"'),
        raw_row(START + 2, "7"),
        raw_row(START + 1, huge),
    ]

    result = fetch_raw_rows(served(rows), "stress", START, END)

    assert [r["value"] for r in result.raw] == [[1, 2], "x", 7, huge]
    # Only the literal that could not be read is counted.
    assert result.notes == [UNDECODABLE_NOTE]


def test_raw_rows_keep_undecodable_value_with_note():
    result = fetch_raw_rows(served([raw_row(START, "not json")]), "stress", START, END)

    assert result.raw == [raw_row(START, "not json")]
    assert result.notes == [UNDECODABLE_NOTE]


def test_raw_rows_keep_a_value_nested_too_deeply_to_decode():
    deep = "[" * 100000 + "]" * 100000  # ``json.loads`` raises RecursionError

    result = fetch_raw_rows(served([raw_row(START, deep)]), "stress", START, END)

    assert result.raw[0]["value"] == deep
    assert result.notes == [UNDECODABLE_NOTE]


@pytest.mark.parametrize("value", [None, 7, ["x"], {"time": START}])
def test_raw_rows_copy_a_value_that_is_not_a_string(value):
    with_value = {**ROW, "time": START + 1, "value": value}
    without_value = {k: v for k, v in ROW.items() if k != "value"}
    without_value["time"] = START

    result = fetch_raw_rows(served([with_value, without_value]), "stress", START, END)

    assert result.raw == [with_value, without_value]
    assert result.raw[0] is not with_value
    assert result.raw[1] is not without_value
    assert result.notes == []


def test_rows_are_copies():
    rows = [raw_row(START + 1), raw_row(START, "not json")]
    before = copy.deepcopy(rows)
    daily = [daily_row(DAY_1), "stray"]
    daily_before = copy.deepcopy(daily)

    raw_result = fetch_raw_rows(served(rows), "stress", START, END)
    daily_result = fetch_daily_rows(served(daily), "valid_stand", DAY_1, DAY_1, tz=UTC)

    # The fixtures still hold their wire strings ...
    assert rows == before
    assert daily == daily_before
    # ... and each returned dict is a new object.
    assert all(out is not src for out, src in zip(raw_result.raw, rows))
    assert daily_result.raw[0] is not daily[0]


def test_raw_rows_note_rows_without_usable_time():
    no_time = {k: v for k, v in ROW.items() if k != "time"}
    rows = [raw_row("x"), no_time, ["not", "a", "row"]]

    result = fetch_raw_rows(served(rows), "stress", START, END)

    assert result.raw == []
    assert result.notes == ["Skipped 3 'stress' row(s) without a usable time."]


def test_raw_rows_note_a_stalled_read():
    result = fetch_raw_rows(served([raw_row(START)], stalled=True), "stress", START, END)

    assert [r["time"] for r in result.raw] == [START]
    assert result.notes == [stalled_note("'stress' rows")]


def test_raw_rows_notes_come_in_a_fixed_order():
    rows = [raw_row(START + 1), raw_row(START, "not json"), raw_row("x")]

    result = fetch_raw_rows(served(rows, stalled=True), "stress", START, END)

    assert result.notes == [
        "Skipped 1 'stress' row(s) without a usable time.",
        UNDECODABLE_NOTE,
        stalled_note("'stress' rows"),
    ]


def test_raw_rows_filter_on_outer_time_only():
    # A daily-type raw key: the outer ``time`` is the UTC midnight of the local date.
    inside = DAY_1
    before = DAY_1 - 86400
    assert before < START <= inside <= END
    kept = {
        "key": "vitality",
        "time": inside,
        # What the value says is not what filters: this date lies before the window.
        "value": json.dumps({"date_time": before}),
    }
    dropped = {
        "key": "vitality",
        "time": before,
        "value": json.dumps({"date_time": inside}),
    }

    result = fetch_raw_rows(served([kept, dropped]), "vitality", START, END)

    assert result.raw == [{**kept, "value": {"date_time": before}}]
    assert result.notes == []


def test_generic_reads_are_not_registered_domains():
    assert fetch_raw_rows not in REGISTRY.values()
    assert fetch_daily_rows not in REGISTRY.values()


# fetch_daily_rows


def test_daily_rows_use_the_report_window():
    fake = served([])

    result = fetch_daily_rows(fake, "valid_stand", DAY_1, DAY_2 + 86399, tz=UTC)

    assert result == DomainResult()
    assert AGGREGATED_ENDPOINT == "data/get_aggregated_fitness_data_by_time"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["endpoint_path"] == AGGREGATED_ENDPOINT
    assert call["list_key"] == "data_list"
    assert call["data_obj"] == {
        "tag": "daily_report",
        "key": "valid_stand",
        "reverse": True,
        "limit": 50,
        "start_time": DAY_1 - 86400,
        "end_time": DAY_2,
    }


def test_daily_rows_pass_another_tag():
    fake = served([])

    fetch_daily_rows(fake, "goal", DAY_1, DAY_1 + 86399, tag="daily_fitness", tz=UTC)

    assert fake.calls[0]["data_obj"]["tag"] == "daily_fitness"
    assert fake.calls[0]["data_obj"]["key"] == "goal"


def test_daily_rows_decode_in_service_order():
    day_0 = DAY_1 - 86400
    rows = [daily_row(t, {"count": n}) for t, n in ((day_0, 1), (DAY_1, 2), (DAY_2, 3))]
    fake = WindowFake(rows)

    result = fetch_daily_rows(fake, "valid_stand", day_0, DAY_2 + 86399, tz=UTC)

    assert [r["time"] for r in result.raw] == [DAY_2, DAY_1, day_0]
    assert [r["value"] for r in result.raw] == [{"count": 3}, {"count": 2}, {"count": 1}]
    assert result.raw[0] == {**DAILY_ROW, "time": DAY_2, "value": {"count": 3}}
    assert result.records == []
    assert result.notes == []


def test_daily_rows_keep_non_object_rows_and_values():
    huge = "9" * 5000
    rows = ["stray", daily_row(DAY_2, "[1]"), daily_row(DAY_1, huge), None]

    result = fetch_daily_rows(served(rows), "valid_stand", DAY_1, DAY_2 + 86399, tz=UTC)

    assert result.raw[0] == "stray"
    assert result.raw[1]["value"] == [1]
    assert result.raw[2]["value"] == huge
    assert result.raw[3] is None
    assert result.notes == [DAILY_UNDECODABLE_NOTE]


def test_daily_rows_note_a_stalled_read():
    fake = served([daily_row(DAY_1)], stalled=True)

    result = fetch_daily_rows(fake, "valid_stand", DAY_1, DAY_1 + 86399, tz=UTC)

    assert [r["time"] for r in result.raw] == [DAY_1]
    assert result.notes == [stalled_note("'valid_stand' daily rows")]


def test_daily_rows_note_order_is_undecodable_then_stalled():
    fake = served([daily_row(DAY_1, "not json")], stalled=True)

    result = fetch_daily_rows(fake, "valid_stand", DAY_1, DAY_1 + 86399, tz=UTC)

    assert result.notes == [
        DAILY_UNDECODABLE_NOTE,
        stalled_note("'valid_stand' daily rows"),
    ]


# argument checks


@pytest.mark.parametrize("key", ["", "  ", None, 5])
def test_key_and_tag_must_be_non_empty_strings(key):
    fake = served([ROW])

    with pytest.raises(ValueError, match="key must be a non-empty string"):
        fetch_raw_rows(fake, key, START, END)
    with pytest.raises(ValueError, match="key must be a non-empty string"):
        fetch_daily_rows(fake, key, START, END)
    assert fake.calls == []


@pytest.mark.parametrize("tag", ["", "  ", None, 5])
def test_tag_must_be_a_non_empty_string(tag):
    fake = served([DAILY_ROW])

    with pytest.raises(ValueError, match="tag must be a non-empty string"):
        fetch_daily_rows(fake, "valid_stand", START, END, tag=tag)
    assert fake.calls == []
