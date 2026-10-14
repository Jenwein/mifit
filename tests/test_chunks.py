"""Chunk policy, planning, serial chunked reads and merge (``mifit.chunks``).

The chunked-read tests run ``MiFit`` over ``WindowFake``, a model of the service's
window rule, so a merged chunked read of every registered domain can be compared
with a single read of the same range: equal records, and equal raw (as a multiset
for sleep, whose raw comes in per-chunk sections). ``workout_detail`` also gets its
link requests and downloads from a ``FakeFdsService`` (``DetailWindowFake``). Every
row and file here is synthetic.
"""

import copy
import functools
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from fds_fixtures import (
    RUN_FLAGS_LIVE,
    FakeFdsService,
    StoreResponse,
    file_key,
    gps_file,
    gps_point,
    record_file,
    run_point,
    run_segment,
    sport_row,
    sport_value,
)

import mifit  # noqa: F401  (importing the package registers every domain)
from mifit import chunks as chunks_module
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
from mifit.domains.raw_window import RAW_ENDPOINT
from mifit.fds import download as download_module
from mifit.fds.ids import GPS, RECORD
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


def daily_reports(figures):
    """One daily report per date, keyed at that date's UTC midnight; ``figures(n)``
    is the n-th day's value."""
    return [
        {"time": utc_midnight(day), "value": json.dumps(figures(n))}
        for n, day in enumerate(row_days())
    ]


def raw_points(figures):
    """Four samples a day, the first at the local midnight, so every block boundary
    has a sample exactly on it; ``figures(n, moment)`` is the n-th sample's value."""
    rows = []
    for day in row_days():
        for k in range(4):
            moment = local_midnight(day) + k * 21600
            value = figures(len(rows), moment)
            rows.append({"time": moment, "value": json.dumps(value)})
    return rows


def steps_rows():
    return daily_reports(
        lambda n: {"steps": 1000 + n, "distance": 700 + n, "calories": 30 + n}
    )


def workout_rows():
    """A workout at local noon every third day, and one at the local midnight of
    each first day of the month blocks of START..END and of the day after END."""
    edges = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 4), date(2026, 3, 16)]
    starts = [local_midnight(day) for day in edges] + [
        int(datetime.combine(day, time(12)).timestamp())
        for n, day in enumerate(row_days())
        if n % 3 == 0
    ]
    rows = []
    for n, start in enumerate(sorted(starts)):
        value = {
            "start_time": start,
            "end_time": start + 1800,
            "duration": 1800,
            "sport_type": 1,
            "calories": 100 + n,
        }
        rows.append({"time": start, "value": json.dumps(value)})
    return rows


def sleep_fake():
    """Daily sleep reports, each with the night that ended that morning, and on the
    raw endpoint each night's session with its stages, keyed at its bedtime."""
    daily, raw = [], []
    for n, day in enumerate(row_days()):
        bedtime = local_midnight(day) - 7200 + n * 60
        wake_up = local_midnight(day) + 21600
        asleep = (wake_up - bedtime) // 60
        stages = {
            "sleep_deep_duration": 90,
            "sleep_light_duration": asleep - 90,
            "sleep_rem_duration": 0,
            "sleep_awake_duration": 0,
        }
        session = {"bedtime": bedtime, "wake_up_time": wake_up, "duration": asleep}
        report = {"total_duration": asleep, **stages}
        report["segment_details"] = [{**session, **stages}]
        daily.append({"time": utc_midnight(day), "value": json.dumps(report)})
        items = [
            {"start_time": bedtime, "end_time": bedtime + 5400, "state": 2},
            {"start_time": bedtime + 5400, "end_time": wake_up, "state": 3},
        ]
        value = {"bedtime": bedtime, "wake_up_time": wake_up, "items": items}
        raw.append({"time": bedtime, "value": json.dumps(value)})
    return WindowFake(daily, by_endpoint={RAW_ENDPOINT: raw})


def consumption_rows():
    """One day's answer per date, under the day's local midnight (``get_json``)."""
    return [
        {
            "time": local_midnight(day),
            "result": {"basal": 1500.0 + n, "exercise": 300.0 + n, "intake": 0},
        }
        for n, day in enumerate(row_days())
    ]


def heart_rate_daily_figures(n):
    return {
        "avg_hr": 60 + n % 20,
        "max_hr": 120 + n % 30,
        "min_hr": 45 + n % 10,
        "avg_rhr": 50 + n % 10,
    }


def spo2_daily_figures(n):
    return {"avg_spo2": 95 + n % 4, "max_spo2": 99, "min_spo2": 90 + n % 5}


def heart_rate_sample(n, moment):
    return {"time": moment, "bpm": 55 + n % 60, "type": 1}


def spo2_sample(n, moment):
    return {"time": moment, "spo2": 90 + n % 10}


def weight_sample(n, moment):
    return {"time": moment, "weight": 70 + n % 10 / 10, "bmi": 22.5}


class StoreFromService:
    """An ``http`` for the downloader: each GET answers 200 with the body
    ``service.fetch`` gives for that link."""

    def __init__(self, service):
        self._service = service

    def get(self, url, **kwargs):
        return StoreResponse(200, [self._service.fetch(url)], None)


class DetailWindowFake(WindowFake):
    """``WindowFake`` over workout list rows whose ``get_json`` answers each
    ``gen_download_url`` request from a ``FakeFdsService`` over ``files``.

    ``store`` is the matching ``http`` for the downloader (``MiFit(fds_http=...)``).
    Each link request is recorded in ``json_calls``, as ``WindowFake`` records its
    one-day reads.
    """

    def __init__(self, rows, files):
        super().__init__(rows)
        self.service = FakeFdsService(files)
        self.store = StoreFromService(self.service)

    def get_json(self, endpoint_path, data_obj=None, **kwargs):
        self.json_calls.append(
            {"endpoint_path": endpoint_path, "data_obj": copy.deepcopy(data_obj)}
        )
        return {"code": 0, "message": "ok", "result": self.service.answer(data_obj)}


def detail_fake(starts):
    """A ``DetailWindowFake`` with one run (format 22) starting at each of ``starts``,
    each with a record file and a GPS file whose figures differ from run to run."""
    rows, files = [], {}
    for n, start in enumerate(sorted(starts)):
        value = sport_value(proto=22, sport=1, time=start, start_time=start)
        rows.append(sport_row(value))
        points = [run_point(heart_rate=100 + n % 100), run_point(heart_rate=101 + n % 100)]
        segment = run_segment(points, resume=start)
        files[file_key(value, RECORD)] = record_file(start, 32, 22, 8, RUN_FLAGS_LIVE, segment)
        fixes = [gps_point(start, 20.0 + n / 100, 10.0), gps_point(start + 1, 20.0, 10.0 + n / 100)]
        files[file_key(value, GPS)] = gps_file(start, 32, 22, *fixes)
    return DetailWindowFake(rows, files)


def workout_detail_world():
    """Eight runs in START..END, four of them at the local midnight that starts a week
    chunk (the first one at START's), and one run on each side of the range."""
    edges = [date(2026, 1, 1), date(2026, 1, 8), date(2026, 2, 5), date(2026, 3, 12)]
    noons = [date(2026, 1, 20), date(2026, 2, 14), date(2026, 3, 1), date(2026, 3, 15)]
    starts = [local_midnight(day) for day in [*edges, date(2026, 3, 16)]] + [
        int(datetime.combine(day, time(12)).timestamp()) for day in [ROWS_FIRST, *noons]
    ]
    return detail_fake(starts)


COMPARISONS = [
    ("steps", lambda: WindowFake(steps_rows()), {"data_list"}),
    (
        "heart_rate_daily",
        lambda: WindowFake(daily_reports(heart_rate_daily_figures)),
        {"data_list"},
    ),
    (
        "spo2_daily",
        lambda: WindowFake(daily_reports(spo2_daily_figures)),
        {"data_list"},
    ),
    ("heart_rate", lambda: WindowFake(raw_points(heart_rate_sample)), {"data_list"}),
    ("spo2", lambda: WindowFake(raw_points(spo2_sample)), {"data_list"}),
    ("weight", lambda: WindowFake(raw_points(weight_sample)), {"data_list"}),
    ("workouts", lambda: WindowFake(workout_rows()), {"sport_records"}),
    # Eight supported workouts: within the 10-workout cap of a single call.
    ("workout_detail", workout_detail_world, {"sport_records"}),
    ("sleep", sleep_fake, {"data_list"}),
    ("consumption", lambda: WindowFake(consumption_rows()), set()),
]
"""``(domain, make_fake, list_keys)`` for every registered domain, each over rows
that run past the read range on both sides."""


def raw_as_multiset(raw):
    return sorted(raw, key=lambda value: json.dumps(value, sort_keys=True))


@pytest.fixture
def windowed(monkeypatch):
    """Return ``build(fake) -> MiFit``: a client whose transport is ``fake``.

    A fake with a ``store`` (``DetailWindowFake``) also serves the client's file
    downloads, which run without their pause here.
    """
    monkeypatch.setattr(download_module.time, "sleep", lambda seconds: None)

    def _build(fake):
        monkeypatch.setattr(client_module, "Transport", lambda *args, **kwargs: fake)
        return MiFit(synthetic_session(), fds_http=getattr(fake, "store", None))

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


def test_chunk_size_per_domain():
    assert CHUNK_DAYS == {"day": 1, "week": 7, "month": 31}
    week = {"heart_rate", "workout_detail"} | ({"spo2"} if "spo2" in REGISTRY else set())
    for domain, size in DOMAIN_CHUNK.items():
        assert size == ("week" if domain in week else "month"), domain
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


def test_the_comparisons_cover_every_registered_domain():
    assert {domain for domain, _, _ in COMPARISONS} == set(REGISTRY)


@pytest.mark.parametrize(
    ("domain", "make_fake", "list_keys"),
    COMPARISONS,
    ids=[domain for domain, _, _ in COMPARISONS],
)
def test_read_chunks_matches_single_read(windowed, domain, make_fake, list_keys):
    fake = make_fake()
    client = windowed(fake)
    plan = plan_chunks(domain, date.fromisoformat(START), date.fromisoformat(END))

    merged = merge_chunks(list(client.read_chunks(domain, START, END)))
    chunked_calls, chunked_days = len(fake.calls), list(fake.json_calls)
    single = getattr(client, domain)(START, END)
    single_calls = len(fake.calls) - chunked_calls
    single_days = fake.json_calls[len(chunked_days) :]

    assert len(plan) > 1
    # Each chunk sends a single read's requests, and every one-day request
    # (consumption) or link request (workout_detail) is sent once, in the single
    # read's order.
    assert chunked_calls == len(plan) * single_calls
    assert chunked_days == single_days
    assert {call["list_key"] for call in fake.calls} == list_keys
    assert single.records  # the comparison is not between two empty reads
    if domain == "workout_detail":
        # Every file was read, so the samples and tracks are compared too.
        assert all(detail.samples and detail.track for detail in single.records)
    assert merged.records == single.records
    if domain == "sleep":
        # Sleep's raw is two sections, the daily values and then the matched raw
        # sessions; merged, those sections come once per chunk (merge_chunks).
        assert merged.raw != single.raw
        assert raw_as_multiset(merged.raw) == raw_as_multiset(single.raw)
    else:
        assert merged.raw == single.raw
    assert merged.notes == single.notes == []


def test_workout_detail_above_the_cap_reads_more_in_chunks(windowed):
    # Twelve runs, one a day at local noon from 02-01: one call reads the newest 10,
    # while each week chunk holds at most 7 and reads them all (chunks module docstring).
    days = [date(2026, 2, 1) + timedelta(days=n) for n in range(12)]
    fake = detail_fake(int(datetime.combine(day, time(12)).timestamp()) for day in days)
    client = windowed(fake)

    parts = list(client.read_chunks("workout_detail", "2026-02-01", "2026-02-14"))
    merged = merge_chunks(parts)
    single = client.workout_detail("2026-02-01", "2026-02-14")

    assert [chunk.label for chunk, _ in parts] == [
        "2026-02-08..2026-02-14",
        "2026-02-01..2026-02-07",
    ]
    # The same workouts in the same order, and the newest 10 read alike.
    assert [d.workout for d in merged.records] == [d.workout for d in single.records]
    assert len(single.records) == 12
    assert merged.records[:10] == single.records[:10]
    assert merged.raw[:10] == single.raw[:10]
    # The single call leaves the oldest two unread and says so; the chunks read them.
    for detail, raw in zip(single.records[10:], single.raw[10:]):
        assert detail.samples is None and detail.track is None
        assert raw["record"] is None and raw["gps"] is None
    for detail, raw in zip(merged.records[10:], merged.raw[10:]):
        assert detail.samples and detail.track
        assert raw["record"] and raw["gps"]
    assert single.notes == [
        "Read the detail of the newest 10 supported workouts only; 2 older one(s) in "
        "the range have no samples or track: ask for a shorter range."
    ]
    assert merged.notes == []


def test_read_chunks_forwards_options_to_every_chunk():
    class Stub:
        def __init__(self):
            self.calls = []

        def workout_detail(self, start, end, **kwargs):
            self.calls.append((start, end, kwargs))
            return DomainResult()

    with_options, without = Stub(), Stub()

    list(
        chunks_module.read_chunks(
            with_options, "workout_detail", "2026-10-01", "2026-10-14", options={"track": False}
        )
    )
    list(chunks_module.read_chunks(without, "workout_detail", "2026-10-01", "2026-10-14"))

    assert with_options.calls == [
        ("2026-10-08", "2026-10-14", {"track": False}),
        ("2026-10-01", "2026-10-07", {"track": False}),
    ]
    assert without.calls == [
        ("2026-10-08", "2026-10-14", {}),
        ("2026-10-01", "2026-10-07", {}),
    ]


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
    client = windowed(WindowFake(rows))

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
    fake = WindowFake(steps_rows())
    client = windowed(fake)
    done = {"2026-03-04..2026-03-15"}

    parts = list(client.read_chunks("steps", START, END, skip=done))

    assert [chunk.label for chunk, _ in parts] == [
        "2026-02-01..2026-03-03",
        "2026-01-01..2026-01-31",
    ]
    assert len(fake.calls) == 2
    assert [len(result.records) for _, result in parts] == [31, 31]


def test_read_chunks_is_lazy(windowed):
    fake = WindowFake(steps_rows())
    client = windowed(fake)

    parts = client.read_chunks("steps", START, END)
    assert fake.calls == []

    chunk, result = next(parts)
    assert len(fake.calls) == 1
    assert chunk.label == "2026-03-04..2026-03-15"
    assert result.records[0].date == "2026-03-15"


def test_read_chunks_rejects_unknown_domain_int_dates_and_reversed_range(windowed):
    fake = WindowFake(steps_rows())
    client = windowed(fake)

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
