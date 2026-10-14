"""Chunk policy, planning, serial chunked reads and merge (``mifit.chunks``).

The chunked-read tests run ``MiFit`` over ``WindowFake``, a model of the service's
window rule, so a merged chunked read can be compared with a single read of the
same range. Every row here is synthetic.
"""

import functools
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

import mifit  # noqa: F401  (importing the package registers every domain)
from mifit import client as client_module
from mifit import transport as transport_module
from mifit.chunks import (
    CHUNK_DAYS,
    DOMAIN_CHUNK,
    OLDEST_FIRST,
    Chunk,
    chunk_size_for,
    merge_chunks,
    plan_chunks,
    split_days,
)
from mifit.client import MiFit
from mifit.domains.base import REGISTRY, DomainResult
from mifit.domains.days import local_midnight
from support import EncryptingHttp, WindowFake, synthetic_session

ROWS_FIRST = date(2025, 12, 31)
ROWS_LAST = date(2026, 3, 16)
"""The synthetic rows run one day past the read range on each side."""
START, END = "2026-01-01", "2026-03-15"


def labels(chunks):
    return [chunk.label for chunk in chunks]


def row_days():
    day = ROWS_FIRST
    while day <= ROWS_LAST:
        yield day
        day += timedelta(days=1)


def utc_midnight(day):
    return int(datetime.combine(day, time(), timezone.utc).timestamp())


def steps_rows():
    """One daily report per date, keyed at that date's UTC midnight."""
    return [
        {
            "time": utc_midnight(day),
            "value": json.dumps(
                {"steps": 1000 + n, "distance": 700 + n, "calories": 30 + n}
            ),
        }
        for n, day in enumerate(row_days())
    ]


def heart_rate_daily_rows():
    """One daily report per date, keyed at that date's UTC midnight."""
    return [
        {
            "time": utc_midnight(day),
            "value": json.dumps(
                {
                    "avg_hr": 60 + n % 20,
                    "max_hr": 120 + n % 30,
                    "min_hr": 45 + n % 10,
                    "avg_rhr": 50 + n % 10,
                }
            ),
        }
        for n, day in enumerate(row_days())
    ]


def heart_rate_rows():
    """Four samples a day, the first at the local midnight, so every block boundary
    has a sample exactly on it."""
    rows = []
    for day in row_days():
        for k in range(4):
            moment = local_midnight(day) + k * 21600
            value = {"time": moment, "bpm": 55 + len(rows) % 60, "type": 1}
            rows.append({"time": moment, "value": json.dumps(value)})
    return rows


def workout_rows():
    """A workout at local noon every third day."""
    rows = []
    for n, day in enumerate(row_days()):
        if n % 3:
            continue
        noon = int(datetime.combine(day, time(12)).timestamp())
        value = {
            "start_time": noon,
            "end_time": noon + 1800,
            "duration": 1800,
            "sport_type": 1,
            "calories": 100 + n,
        }
        rows.append({"time": noon, "value": json.dumps(value)})
    return rows


@pytest.fixture
def windowed(monkeypatch):
    """Return ``build(rows) -> (MiFit, WindowFake)``: a client whose transport is
    one ``WindowFake`` serving ``rows``."""

    def _build(rows):
        fake = WindowFake(rows)
        monkeypatch.setattr(client_module, "Transport", lambda *args, **kwargs: fake)
        return MiFit(synthetic_session()), fake

    return _build


def test_split_days_month_blocks_count_from_the_first_day():
    chunks = split_days(date(2026, 1, 1), date(2026, 3, 15), "month")

    assert labels(chunks) == [
        "2026-01-01..2026-01-31",
        "2026-02-01..2026-03-03",
        "2026-03-04..2026-03-15",
    ]


def test_split_days_week_blocks_cross_the_year():
    chunks = split_days(date(2025, 12, 30), date(2026, 1, 12), "week")

    assert labels(chunks) == ["2025-12-30..2026-01-05", "2026-01-06..2026-01-12"]


def test_range_within_one_block_is_one_chunk():
    assert split_days(date(2026, 10, 1), date(2026, 10, 31), "month") == [
        Chunk(date(2026, 10, 1), date(2026, 10, 31))
    ]
    assert split_days(date(2026, 10, 5), date(2026, 10, 5), "week") == [
        Chunk(date(2026, 10, 5), date(2026, 10, 5))
    ]
    assert labels(split_days(date(2026, 10, 1), date(2026, 10, 3), "day")) == [
        "2026-10-01..2026-10-01",
        "2026-10-02..2026-10-02",
        "2026-10-03..2026-10-03",
    ]


def test_split_days_rejects_reversed_range_and_unknown_size():
    with pytest.raises(ValueError):
        split_days(date(2026, 3, 15), date(2026, 1, 1), "week")
    with pytest.raises(ValueError, match="year"):
        split_days(date(2026, 1, 1), date(2026, 3, 15), "year")


def test_plan_is_newest_first_except_consumption():
    first, last = date(2026, 1, 1), date(2026, 3, 15)

    assert labels(plan_chunks("steps", first, last)) == [
        "2026-03-04..2026-03-15",
        "2026-02-01..2026-03-03",
        "2026-01-01..2026-01-31",
    ]
    assert labels(plan_chunks("consumption", first, last)) == [
        "2026-01-01..2026-01-31",
        "2026-02-01..2026-03-03",
        "2026-03-04..2026-03-15",
    ]
    assert OLDEST_FIRST == frozenset({"consumption"})
    # An explicit size overrides the domain's.
    assert labels(plan_chunks("steps", first, date(2026, 1, 10), "week")) == [
        "2026-01-08..2026-01-10",
        "2026-01-01..2026-01-07",
    ]


def test_every_registered_domain_has_a_chunk_size():
    assert set(REGISTRY) <= set(DOMAIN_CHUNK)


def test_raw_point_domains_use_week():
    assert CHUNK_DAYS == {"day": 1, "week": 7, "month": 31}
    for domain, size in DOMAIN_CHUNK.items():
        assert size == ("week" if domain in {"heart_rate", "spo2"} else "month"), domain
        assert chunk_size_for(domain) == size
    # A domain missing from the table gets the conservative size.
    assert chunk_size_for("not_a_domain") == "week"


def test_merge_concatenates_and_prefixes_notes():
    newer = Chunk(date(2026, 2, 1), date(2026, 3, 3))
    older = Chunk(date(2026, 1, 1), date(2026, 1, 31))
    parts = [
        (newer, DomainResult(["r3", "r2"], [{"n": 3}, {"n": 2}], ["capped"])),
        (older, DomainResult(["r1"], [{"n": 1}], ["stalled", "skipped"])),
    ]

    merged = merge_chunks(parts)

    assert merged == DomainResult(
        records=["r3", "r2", "r1"],
        raw=[{"n": 3}, {"n": 2}, {"n": 1}],
        notes=[
            "2026-02-01..2026-03-03: capped",
            "2026-01-01..2026-01-31: stalled",
            "2026-01-01..2026-01-31: skipped",
        ],
    )
    # The parts are left as they were.
    assert parts[0][1].notes == ["capped"]


def test_merge_of_one_part_keeps_notes_as_they_are():
    only = Chunk(date(2026, 10, 1), date(2026, 10, 31))
    result = DomainResult(records=["r1"], raw=[{"n": 1}], notes=["capped"])

    assert merge_chunks([(only, result)]) == result
    assert merge_chunks([]) == DomainResult()


@pytest.mark.parametrize(
    ("domain", "rows", "list_key"),
    [
        ("steps", steps_rows, "data_list"),
        ("heart_rate_daily", heart_rate_daily_rows, "data_list"),
        ("heart_rate", heart_rate_rows, "data_list"),
        ("workouts", workout_rows, "sport_records"),
    ],
)
def test_read_chunks_matches_single_read(windowed, domain, rows, list_key):
    client, fake = windowed(rows())
    plan = plan_chunks(domain, date.fromisoformat(START), date.fromisoformat(END))

    merged = merge_chunks(list(client.read_chunks(domain, START, END)))
    chunked_calls = len(fake.calls)
    single = getattr(client, domain)(START, END)

    assert len(plan) > 1
    assert chunked_calls == len(plan)
    assert {call["list_key"] for call in fake.calls} == {list_key}
    assert single.records  # the comparison is not between two empty reads
    assert merged.records == single.records
    assert merged.raw == single.raw
    assert merged.notes == single.notes == []


def test_a_chunk_edge_on_a_fall_back_midnight_loses_no_row(windowed, monkeypatch):
    # In Santiago the clocks go back an hour at the midnight that ends 2026-04-04, so
    # 23:00 to 23:59:59 of that day happens twice; the week chunks meet right there.
    try:
        santiago = ZoneInfo("America/Santiago")
    except ZoneInfoNotFoundError:
        pytest.skip("IANA timezone database not available")
    in_santiago = functools.partial(client_module.to_epoch_s, tz=santiago)
    monkeypatch.setattr(client_module, "to_epoch_s", in_santiago)
    first_row = int(datetime(2026, 3, 28, tzinfo=timezone.utc).timestamp())
    rows = []
    for n in range(16 * 48):  # every half hour for 16 days, by the UTC clock
        moment = first_row + n * 1800
        value = {"time": moment, "bpm": 60 + n % 40, "type": 1}
        rows.append({"time": moment, "value": json.dumps(value)})
    client, _ = windowed(rows)

    parts = list(client.read_chunks("heart_rate", "2026-03-29", "2026-04-11"))
    merged = merge_chunks(parts)
    single = client.heart_rate("2026-03-29", "2026-04-11")

    assert [chunk.label for chunk, _ in parts] == [
        "2026-04-05..2026-04-11",
        "2026-03-29..2026-04-04",
    ]
    times = [point.epoch_s for point in merged.records]
    # 03:00Z and 03:30Z are the second 23:00 and 23:30 of 04-04; 04:00Z is the
    # local midnight that starts 04-05.
    assert {1775358000, 1775359800} <= set(times)
    assert times.count(1775361600) == 1
    assert merged.records == single.records


def test_read_chunks_skips_done_labels(windowed):
    client, fake = windowed(steps_rows())
    done = {"2026-03-04..2026-03-15"}

    parts = list(client.read_chunks("steps", START, END, skip=done))

    assert [chunk.label for chunk, _ in parts] == [
        "2026-02-01..2026-03-03",
        "2026-01-01..2026-01-31",
    ]
    assert len(fake.calls) == 2
    assert [len(result.records) for _, result in parts] == [31, 31]


def test_read_chunks_is_lazy(windowed):
    client, fake = windowed(steps_rows())

    parts = client.read_chunks("steps", START, END)
    assert fake.calls == []

    chunk, result = next(parts)
    assert len(fake.calls) == 1
    assert chunk.label == "2026-03-04..2026-03-15"
    assert result.records[0].date == "2026-03-15"


def test_read_chunks_rejects_unknown_domain_int_dates_and_reversed_range(windowed):
    client, fake = windowed(steps_rows())

    # Arguments are checked when the read is set up, before any iteration.
    with pytest.raises(ValueError, match="steps") as excinfo:
        client.read_chunks("profile", START, END)
    assert "profile" in str(excinfo.value)
    with pytest.raises(TypeError, match="date"):
        client.read_chunks("steps", 1767225600, 1773532800)
    with pytest.raises(TypeError, match="date"):
        client.read_chunks("steps", START, None)
    for bad in ("2026-02-30", "2026-1-1", "20260101"):
        with pytest.raises(ValueError, match="YYYY-MM-DD|calendar date"):
            client.read_chunks("steps", bad, END)
    with pytest.raises(ValueError, match="after"):
        client.read_chunks("steps", END, START)
    with pytest.raises(ValueError, match="year"):
        client.read_chunks("steps", START, END, size="year")
    assert fake.calls == []


def test_chunks_share_one_paced_transport(monkeypatch):
    events = []
    monkeypatch.setattr(
        transport_module.time, "sleep", lambda seconds: events.append(("sleep", seconds))
    )
    monkeypatch.setattr(random, "uniform", lambda low, high: 0.0)
    http = EncryptingHttp(
        [{"code": 0, "result": {"data_list": [], "has_more": False}}], events=events
    )
    client = MiFit(synthetic_session(), http=http, auto_renew=False)

    parts = list(client.read_chunks("steps", START, END))

    assert len(parts) == 3
    assert events == [("sleep", 1.0), ("get", None)] * 3
