"""Sleep domain tests. A fake ``Transport`` serves synthetic payloads; nothing here
touches the network and no real health data is used.

The fixtures follow the live-verified shapes (plan 2026-10-14-mifit-sleep-domain):
a daily report row's ``time`` is the UTC midnight of the local date it stands for
and its ``value`` is a JSON string with the day's totals and ``segment_details`` (one
per sleep session, the sessions that end on that day). Day A (2025-10-14) holds a
night and a nap; day B (2025-10-15) only a nap, with HR/SpO2 reported as ``0``.
"""

import dataclasses
import json
from datetime import date, timezone
from pathlib import Path

from mifit.domains.base import REGISTRY
from mifit.domains.days import local_midnight
from mifit.domains.sleep import fetch_sleep
from mifit.models.sleep import SleepRecord, SleepSession
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
    transport = FakeTransport(daily or load("sleep_daily_sample.json"), raw)
    return transport, fetch_sleep(transport, START, END, tz=UTC)


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
    assert night == SleepSession(
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
    # A zero duration is a real value, not a missing one.
    assert (record.asleep_min, record.deep_min, record.awake_min) == (40, 0, 0)


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
