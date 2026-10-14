"""Consumption (calories) domain tests. A fake ``Transport`` serves a scripted
response per request ``time``; nothing here touches the network and no real
health data is used.

Live-verified shape: ``GET app/v1/diet/user_consumption`` takes ``data`` =
``{"time": <Unix seconds of the target day's local midnight>}`` and answers, under
the ``result`` wrapper of the decrypted envelope, ``{"basal", "exercise", "intake"}``
(resting/BMR, active, and food calories). One call covers one day, so the domain
walks the days itself.

Most tests pin ``tz=UTC`` so a day's local midnight is simply its UTC midnight; the
timezone tests pin other offsets, and one checks the machine-local default.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from mifit.domains.base import REGISTRY
from mifit.domains.consumption import fetch_consumption
from mifit.errors import ApiError, AuthError
from mifit.models.consumption import ConsumptionRecord
from mifit.transport import Transport

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
DAY_3 = DAY_2 + 86400
ENDPOINT = "diet/user_consumption"
UTC = timezone.utc
CST = timezone(timedelta(hours=8))  # the live-verified account's zone
EST = timezone(timedelta(hours=-5))


class FakeTransport(Transport):
    """Replaces ``get_json`` with a scripted response per request ``time``.

    ``responses`` maps the ``time`` the domain sends to either a payload dict to
    return or an exception instance to raise. Every call is recorded.
    """

    def __init__(self, responses):
        # No Session/http needed: ``get_json`` is fully replaced below.
        self._responses = responses
        self.calls = []

    def get_json(self, endpoint_path, data_obj, *, sign_path=None):
        self.calls.append({"endpoint_path": endpoint_path, "data_obj": dict(data_obj)})
        scripted = self._responses[data_obj["time"]]
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


def envelope(basal=1907.5, exercise=365, intake=0):
    """A decrypted payload in the live shape: business data under ``result``."""
    return {
        "code": 0,
        "message": "success",
        "result": {"basal": basal, "exercise": exercise, "intake": intake},
    }


def test_registered_under_consumption():
    assert REGISTRY["consumption"] is fetch_consumption


def test_one_day_maps_fields():
    transport = FakeTransport({DAY_1: envelope(1907.5, 365, 0)})

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    assert result.records == [
        ConsumptionRecord(
            date="2025-10-14",
            epoch_s=DAY_1,
            resting_kcal=1907.5,
            active_kcal=365,
            intake_kcal=0,
            total_kcal=2272.5,
        )
    ]
    assert result.raw == [{"basal": 1907.5, "exercise": 365, "intake": 0}]
    assert result.notes == []


def test_range_yields_one_record_per_day_with_correct_time():
    transport = FakeTransport(
        {
            DAY_1: envelope(1900, 100, 500),
            DAY_2: envelope(1910, 200, 600),
            DAY_3: envelope(1920, 300, 700),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_3, tz=UTC)

    assert [c["endpoint_path"] for c in transport.calls] == [ENDPOINT] * 3
    assert [c["data_obj"] for c in transport.calls] == [
        {"time": DAY_1},
        {"time": DAY_2},
        {"time": DAY_3},
    ]
    assert [r.date for r in result.records] == ["2025-10-14", "2025-10-15", "2025-10-16"]
    assert [r.epoch_s for r in result.records] == [DAY_1, DAY_2, DAY_3]
    assert [r.resting_kcal for r in result.records] == [1900, 1910, 1920]
    assert [r.active_kcal for r in result.records] == [100, 200, 300]
    assert [r.intake_kcal for r in result.records] == [500, 600, 700]
    assert [r.total_kcal for r in result.records] == [2000, 2110, 2220]
    assert len(result.raw) == 3
    assert result.notes == []


def test_requests_local_midnight_east_of_utc():
    # Regression (live, UTC+8): the service files a request ``time`` under the day the
    # *app* would mean by it, and the app sends the local midnight of the wanted day
    # (``TimeDateUtil.atStartOfDay`` in the device zone). A UTC-noon ``time`` was
    # answered with the NEXT local day's figures, so every record was a day off.
    start = DAY_1 + 16 * 3600  # local 2025-10-15 00:00 (UTC+8)
    end = DAY_2 + 16 * 3600 + 86399  # local 2025-10-16 23:59:59
    transport = FakeTransport({start: envelope(), DAY_2 + 16 * 3600: envelope()})

    result = fetch_consumption(transport, start, end, tz=CST)

    assert [c["data_obj"] for c in transport.calls] == [
        {"time": DAY_1 + 16 * 3600},
        {"time": DAY_2 + 16 * 3600},
    ]
    assert [r.date for r in result.records] == ["2025-10-15", "2025-10-16"]
    assert [r.epoch_s for r in result.records] == [DAY_1 + 16 * 3600, DAY_2 + 16 * 3600]
    assert result.notes == []


def test_requests_local_midnight_west_of_utc():
    local_midnight = DAY_1 + 5 * 3600  # local 2025-10-14 00:00 (UTC-5)
    transport = FakeTransport({local_midnight: envelope()})

    result = fetch_consumption(transport, local_midnight, local_midnight, tz=EST)

    assert [c["data_obj"]["time"] for c in transport.calls] == [local_midnight]
    assert [r.date for r in result.records] == ["2025-10-14"]


def test_any_instant_queries_its_local_days_midnight():
    # 2025-10-15T12:00Z is 20:00 on local 2025-10-15 (UTC+8): ask for that day's
    # local midnight, never for the instant itself.
    transport = FakeTransport({DAY_1 + 16 * 3600: envelope()})

    result = fetch_consumption(transport, DAY_2 + 12 * 3600, DAY_2 + 12 * 3600, tz=CST)

    assert [c["data_obj"]["time"] for c in transport.calls] == [DAY_1 + 16 * 3600]
    assert [r.date for r in result.records] == ["2025-10-15"]


def test_end_of_day_bound_does_not_include_the_next_day():
    start = DAY_1 + 16 * 3600  # local 2025-10-15 00:00 (UTC+8)
    transport = FakeTransport({start: envelope()})

    result = fetch_consumption(transport, start, start + 86399, tz=CST)

    assert [c["data_obj"]["time"] for c in transport.calls] == [start]
    assert [r.date for r in result.records] == ["2025-10-15"]


def test_default_timezone_is_the_machines_local_zone():
    # Like the app, which uses the device zone. Expected values are derived from the
    # local zone, so this holds whatever zone the test machine is in.
    local_day = datetime.fromtimestamp(DAY_1).date()
    local_midnight = int(datetime.combine(local_day, time()).timestamp())
    transport = FakeTransport({local_midnight: envelope()})

    result = fetch_consumption(transport, DAY_1, DAY_1)

    assert [c["data_obj"]["time"] for c in transport.calls] == [local_midnight]
    assert [r.date for r in result.records] == [local_day.isoformat()]
    assert [r.epoch_s for r in result.records] == [local_midnight]


def test_days_are_calendar_days_across_a_dst_change():
    # New York leaves DST on 2025-11-02, a 25-hour day: stepping by a fixed 86400 s
    # would drift off local midnight. Needs the IANA database (tzdata).
    try:
        new_york = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        pytest.skip("IANA timezone database not available")
    nov_1 = 1761969600  # 2025-11-01T04:00Z = local midnight (EDT, UTC-4)
    nov_2 = nov_1 + 86400  # 2025-11-02T04:00Z: midnight is still EDT
    nov_3 = nov_2 + 90000  # 2025-11-03T05:00Z: EST, after the 25-hour day
    transport = FakeTransport({nov_1: envelope(), nov_2: envelope(), nov_3: envelope()})

    result = fetch_consumption(transport, nov_1, nov_3, tz=new_york)

    assert [c["data_obj"]["time"] for c in transport.calls] == [nov_1, nov_2, nov_3]
    assert [r.date for r in result.records] == ["2025-11-01", "2025-11-02", "2025-11-03"]


def test_end_before_start_queries_nothing():
    transport = FakeTransport({})

    result = fetch_consumption(transport, DAY_2, DAY_1, tz=UTC)

    assert transport.calls == []
    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_missing_day_is_noted_not_fabricated():
    transport = FakeTransport(
        {
            DAY_1: envelope(1900, 100, 500),
            # A day whose result carries no usable figures at all.
            DAY_2: {"code": 0, "result": {}},
            DAY_3: envelope(1920, 300, 700),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_3, tz=UTC)

    assert [r.date for r in result.records] == ["2025-10-14", "2025-10-16"]
    assert len(result.notes) == 1
    assert "2025-10-15" in result.notes[0]
    # The empty result is still kept as raw, so raw lines up with what was received.
    assert result.raw == [
        {"basal": 1900, "exercise": 100, "intake": 500},
        {},
        {"basal": 1920, "exercise": 300, "intake": 700},
    ]


def test_api_error_day_is_noted_and_other_days_still_returned():
    transport = FakeTransport(
        {
            DAY_1: ApiError("invalid time", code=1001),
            DAY_2: envelope(1910, 200, 600),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_2, tz=UTC)

    assert [r.date for r in result.records] == ["2025-10-15"]
    assert len(result.raw) == 1  # nothing was received for the failed day
    assert len(result.notes) == 1
    assert "2025-10-14" in result.notes[0]


def test_auth_error_is_not_swallowed():
    transport = FakeTransport({DAY_1: AuthError("rejected")})

    with pytest.raises(AuthError):
        fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)


def test_values_read_from_result_wrapper():
    # Decoy figures at the top level must be ignored; only ``result`` counts.
    payload = {
        "code": 0,
        "basal": 1.0,
        "exercise": 2.0,
        "intake": 3.0,
        "result": {"basal": 1907.5, "exercise": 365, "intake": 120},
    }
    transport = FakeTransport({DAY_1: payload})

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    (record,) = result.records
    assert record.resting_kcal == 1907.5
    assert record.active_kcal == 365
    assert record.intake_kcal == 120
    assert result.raw == [{"basal": 1907.5, "exercise": 365, "intake": 120}]


def test_payload_without_result_wrapper_falls_back_to_top_level():
    transport = FakeTransport(
        {DAY_1: {"basal": 1907.5, "exercise": 365, "intake": 0}}
    )

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    (record,) = result.records
    assert record.resting_kcal == 1907.5
    assert record.total_kcal == 2272.5


def test_zero_intake_is_a_value_not_missing():
    transport = FakeTransport({DAY_1: envelope(1907.5, 0, 0)})

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    (record,) = result.records
    assert record.intake_kcal == 0
    assert record.active_kcal == 0
    assert record.total_kcal == 1907.5
    assert result.notes == []


def test_total_is_none_unless_resting_and_active_are_both_present():
    transport = FakeTransport(
        {
            DAY_1: {"code": 0, "result": {"basal": 1907.5, "intake": 250}},
            DAY_2: {"code": 0, "result": {"exercise": 365}},
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_2, tz=UTC)

    first, second = result.records
    assert (first.resting_kcal, first.active_kcal, first.intake_kcal) == (
        1907.5,
        None,
        250,
    )
    assert first.total_kcal is None
    assert (second.resting_kcal, second.active_kcal, second.intake_kcal) == (
        None,
        365,
        None,
    )
    assert second.total_kcal is None
    assert result.notes == []


def test_non_numeric_values_are_treated_as_missing():
    bad = {"basal": "n/a", "exercise": None, "intake": True}
    transport = FakeTransport({DAY_1: {"code": 0, "result": bad}})

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    assert result.records == []
    assert len(result.notes) == 1
    assert len(result.raw) == 1


def test_non_object_result_is_noted_and_skipped():
    transport = FakeTransport({DAY_1: {"code": 0, "result": None}})

    result = fetch_consumption(transport, DAY_1, DAY_1, tz=UTC)

    assert result.records == []
    assert result.raw == []
    assert len(result.notes) == 1
