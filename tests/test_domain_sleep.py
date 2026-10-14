"""Sleep domain tests. A fake ``Transport`` serves synthetic payloads; nothing here
touches the network and no real health data is used.

The fixtures follow the live-verified shapes (plan 2026-10-14-mifit-sleep-domain):
a daily report row's ``time`` is the UTC midnight of the local date it stands for
and its ``value`` is a JSON string with the day's totals and ``segment_details`` (one
per sleep session, the sessions that end on that day). Day A (2025-10-14) holds a
night and a nap; day B (2025-10-15) only a nap, with HR/SpO2 reported as ``0``.
The raw fixture holds the matching raw sessions (only the night has stage ``items``)
plus one session from the day before the window.
"""

import dataclasses
import json
from datetime import date, timezone
from pathlib import Path

from mifit.domains.base import REGISTRY
from mifit.domains.days import local_midnight
from mifit.domains.sleep import fetch_sleep
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.transport import Transport

FIXTURES = Path(__file__).parent / "fixtures"
DAILY_ENDPOINT = "data/get_aggregated_fitness_data_by_time"
RAW_ENDPOINT = "data/get_fitness_data_by_time"
UTC = timezone.utc

DAY_A = 1760400000  # 2025-10-14T00:00:00Z
DAY_B = DAY_A + 86400
START = DAY_A
END = DAY_B + 86399
NIGHT_BED = DAY_A + 1800
NIGHT_WAKE = NIGHT_BED + 7 * 3600
NAP_A_BED = DAY_A + 13 * 3600
NAP_A_WAKE = NAP_A_BED + 1800
# Stage boundaries of the night in the raw fixture.
T0, T1, T2, T3, T4 = NIGHT_BED, NIGHT_BED + 3600, NIGHT_BED + 9600, NIGHT_BED + 12000, NIGHT_WAKE


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport(Transport):
    """Serves canned rows per endpoint from ``get_paged`` and records each call."""

    def __init__(self, daily=None, raw=None):
        # No Session/http needed: ``get_paged`` is fully replaced below.
        self._rows = {
            DAILY_ENDPOINT: list((daily or {"result": {}})["result"].get("data_list") or []),
            RAW_ENDPOINT: list((raw or {"result": {}})["result"].get("data_list") or []),
        }
        self.calls = []

    def get_paged(
        self, endpoint_path, data_obj, *, list_key="data_list", sign_path=None, max_pages=None
    ):
        self.calls.append({"endpoint_path": endpoint_path, "data_obj": dict(data_obj)})
        return list(self._rows.get(endpoint_path, []))


def fetch(daily=None, raw=None):
    transport = FakeTransport(
        daily or load("sleep_daily_sample.json"), raw or load("sleep_raw_sample.json")
    )
    return transport, fetch_sleep(transport, START, END, tz=UTC)


def raw_without(predicate):
    """The raw fixture minus the rows whose decoded value matches ``predicate``."""
    raw = load("sleep_raw_sample.json")
    rows = raw["result"]["data_list"]
    raw["result"]["data_list"] = [r for r in rows if not predicate(json.loads(r["value"]))]
    return raw


def night_of(result):
    return record_for(result, "2025-10-14").sessions[0]


def record_for(result, day):
    (record,) = [r for r in result.records if r.date == day]
    return record


def test_sleep_domain_is_registered():
    assert REGISTRY["sleep"] is fetch_sleep


def test_requests_daily_report_sleep_for_the_window():
    transport, _ = fetch()

    assert transport.calls[0] == {
        "endpoint_path": DAILY_ENDPOINT,
        "data_obj": {
            "tag": "daily_report",
            "key": "sleep",
            "reverse": True,
            "limit": 50,
            # (start, end] over the daily-report keys: the day before 2025-10-14
            # through 2025-10-15.
            "start_time": DAY_A - 86400,
            "end_time": DAY_B,
        },
    }


def test_maps_day_totals():
    _, result = fetch()

    record = record_for(result, "2025-10-14")
    assert record.epoch_s == local_midnight(date(2025, 10, 14), UTC)
    assert (record.asleep_min, record.deep_min, record.light_min, record.awake_min) == (
        410,
        100,
        280,
        40,
    )
    assert record.rem_min is None  # absent on the wire
    assert record.score == 72
    assert (record.avg_hr_bpm, record.min_hr_bpm, record.max_hr_bpm) == (61, 52, 80)
    assert (record.avg_spo2_pct, record.min_spo2_pct, record.max_spo2_pct) == (95, 91, 99)


def test_maps_sessions_in_service_order():
    _, result = fetch()

    night, nap = record_for(result, "2025-10-14").sessions
    assert dataclasses.replace(night, stages=[]) == SleepSession(
        start_epoch_s=NIGHT_BED,
        end_epoch_s=NIGHT_WAKE,
        utc_offset_s=28800,
        asleep_min=380,
        deep_min=100,
        light_min=280,
        rem_min=None,
        awake_min=40,
        stages=[],
    )
    assert (nap.start_epoch_s, nap.end_epoch_s, nap.asleep_min) == (NAP_A_BED, NAP_A_WAKE, 30)


def test_zero_hr_and_spo2_become_none():
    _, result = fetch()

    record = record_for(result, "2025-10-15")
    assert (record.avg_hr_bpm, record.min_hr_bpm, record.max_hr_bpm) == (None, None, None)
    assert (record.avg_spo2_pct, record.min_spo2_pct, record.max_spo2_pct) == (None, None, None)
    assert record.asleep_min == 40


def test_unstaged_sleep_has_no_stage_minutes():
    # A nap reports 0 deep/light/awake minutes although it slept: it was not staged,
    # so its stage minutes are unknown (None), not zero.
    _, result = fetch()

    nap_a = record_for(result, "2025-10-14").sessions[1]
    (nap_b,) = record_for(result, "2025-10-15").sessions
    day_b = record_for(result, "2025-10-15")
    for item in (nap_a, nap_b, day_b):
        assert (item.deep_min, item.light_min, item.rem_min, item.awake_min) == (
            None,
            None,
            None,
            None,
        )
    assert (nap_a.asleep_min, nap_b.asleep_min, day_b.asleep_min) == (30, 40, 40)


def test_staged_day_keeps_its_stage_minutes_including_zeros():
    # Day A has a staged night: its figures stand, and a staged zero is a real zero.
    daily = load("sleep_daily_sample.json")
    row = daily["result"]["data_list"][1]
    value = json.loads(row["value"])
    value["sleep_awake_duration"] = 0
    value["segment_details"][0]["sleep_awake_duration"] = 0
    row["value"] = json.dumps(value)

    _, result = fetch(daily)

    day_a = record_for(result, "2025-10-14")
    assert (day_a.deep_min, day_a.light_min, day_a.awake_min) == (100, 280, 0)
    assert day_a.sessions[0].awake_min == 0


def test_raw_keeps_decoded_daily_values():
    daily = load("sleep_daily_sample.json")
    _, result = fetch(daily)

    expected = [json.loads(row["value"]) for row in daily["result"]["data_list"]]
    assert result.raw[:2] == expected


def test_row_with_bad_value_is_skipped_with_note():
    daily = load("sleep_daily_sample.json")
    daily["result"]["data_list"][0]["value"] = "{not json"

    _, result = fetch(daily)

    assert [r.date for r in result.records] == ["2025-10-14"]
    assert len(result.notes) == 1


def test_unverified_fields_stay_out_of_records():
    names = {f.name for f in dataclasses.fields(SleepRecord)} | {
        f.name for f in dataclasses.fields(SleepSession)
    }

    for unverified in ("stage", "evaluation", "awake_count", "uncomplete"):
        assert not [n for n in names if unverified in n and n != "stages"]


def test_requests_raw_sessions_for_the_widened_window():
    transport, _ = fetch()

    assert transport.calls[1] == {
        "endpoint_path": RAW_ENDPOINT,
        "data_obj": {
            "key": "sleep",
            # A day either side of 2025-10-14 00:00 .. 2025-10-16 00:00 (UTC).
            "start_time": DAY_A - 86400,
            "end_time": DAY_B + 2 * 86400,
            "reverse": True,
        },
    }


def test_attaches_stages_to_the_matching_session():
    _, result = fetch()

    assert night_of(result).stages == [
        SleepStage(T0, T1, "light"),
        SleepStage(T1, T2, "deep"),
        SleepStage(T2, T3, "awake"),
        SleepStage(T3, T4, "light"),
    ]
    assert result.notes == []


def test_nap_without_items_has_no_stages_and_no_note():
    _, result = fetch()

    nap = record_for(result, "2025-10-14").sessions[1]
    assert nap.stages == []
    assert result.notes == []


def test_unknown_state_maps_to_unknown():
    raw = load("sleep_raw_sample.json")
    night_row = raw["result"]["data_list"][2]
    value = json.loads(night_row["value"])
    for item, state in zip(value["items"], (0, 6, 9)):
        item["state"] = state
    night_row["value"] = json.dumps(value)

    _, result = fetch(raw=raw)

    assert [s.stage for s in night_of(result).stages] == ["unknown", "unknown", "unknown", "light"]


def test_unmatched_session_keeps_no_stages_and_notes_it():
    _, result = fetch(raw=raw_without(lambda v: v["bedtime"] == NIGHT_BED))

    assert night_of(result).stages == []
    assert len(result.notes) == 1
    assert "2025-10-14" in result.notes[0]


def test_extra_raw_sessions_are_ignored():
    _, result = fetch()

    starts = [s.start_epoch_s for r in result.records for s in r.sessions]
    assert DAY_A - 86400 + 2 * 3600 not in starts
    assert result.notes == []


def test_deleted_raw_rows_are_not_used():
    raw = load("sleep_raw_sample.json")
    raw["result"]["data_list"][2]["deleted"] = True

    _, result = fetch(raw=raw)

    assert night_of(result).stages == []
    assert len(result.notes) == 1


def test_raw_lists_daily_then_session_values():
    daily = load("sleep_daily_sample.json")
    raw = load("sleep_raw_sample.json")

    _, result = fetch(daily, raw)

    assert result.raw == [json.loads(r["value"]) for r in daily["result"]["data_list"]] + [
        json.loads(r["value"]) for r in raw["result"]["data_list"]
    ]
