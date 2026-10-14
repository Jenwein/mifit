"""Consumption (calories) domain tests. A fake ``Transport`` serves a scripted
response per request ``time``; nothing here touches the network and no real
health data is used.

Live-verified shape: ``GET app/v1/diet/user_consumption`` takes ``data`` =
``{"time": <Unix seconds within the target day>}`` and answers, under the
``result`` wrapper of the decrypted envelope, ``{"basal", "exercise", "intake"}``
(resting/BMR, active, and food calories). One call covers one day, so the domain
walks the days itself.
"""

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
NOON = 43200


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
    transport = FakeTransport({DAY_1 + NOON: envelope(1907.5, 365, 0)})

    result = fetch_consumption(transport, DAY_1, DAY_1)

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
            DAY_1 + NOON: envelope(1900, 100, 500),
            DAY_2 + NOON: envelope(1910, 200, 600),
            DAY_3 + NOON: envelope(1920, 300, 700),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_3)

    assert [c["endpoint_path"] for c in transport.calls] == [ENDPOINT] * 3
    assert [c["data_obj"] for c in transport.calls] == [
        {"time": DAY_1 + NOON},
        {"time": DAY_2 + NOON},
        {"time": DAY_3 + NOON},
    ]
    assert [r.date for r in result.records] == ["2025-10-14", "2025-10-15", "2025-10-16"]
    assert [r.epoch_s for r in result.records] == [DAY_1, DAY_2, DAY_3]
    assert [r.resting_kcal for r in result.records] == [1900, 1910, 1920]
    assert [r.active_kcal for r in result.records] == [100, 200, 300]
    assert [r.intake_kcal for r in result.records] == [500, 600, 700]
    assert [r.total_kcal for r in result.records] == [2000, 2110, 2220]
    assert len(result.raw) == 3
    assert result.notes == []


def test_local_midnight_bound_is_labeled_with_its_own_local_date():
    # A UTC+8 user asks for local 2025-10-15: local midnight is 2025-10-14T16:00Z
    # (DAY_1 + 16h). Flooring that to the UTC day would label the record 2025-10-14;
    # rounding to the nearest UTC midnight lands on 2025-10-15, the day asked for.
    local_midnight_plus8 = DAY_1 + 16 * 3600
    transport = FakeTransport({DAY_2 + NOON: envelope(1907.5, 365, 0)})

    result = fetch_consumption(transport, local_midnight_plus8, local_midnight_plus8)

    assert [c["data_obj"] for c in transport.calls] == [{"time": DAY_2 + NOON}]
    (record,) = result.records
    assert record.date == "2025-10-15"
    assert record.epoch_s == DAY_2
    assert result.notes == []


def test_local_midnight_range_east_of_utc_keeps_the_requested_dates():
    # UTC+8: local midnights of 2025-10-15 and 2025-10-16 (the facade passes the end
    # date's own local midnight, not its last second).
    start = DAY_1 + 16 * 3600
    end = DAY_2 + 16 * 3600
    transport = FakeTransport({DAY_2 + NOON: envelope(), DAY_3 + NOON: envelope()})

    result = fetch_consumption(transport, start, end)

    assert [c["data_obj"]["time"] for c in transport.calls] == [
        DAY_2 + NOON,
        DAY_3 + NOON,
    ]
    assert [r.date for r in result.records] == ["2025-10-15", "2025-10-16"]


def test_local_midnight_bound_west_of_utc_keeps_the_requested_date():
    # UTC-5: local midnight of 2025-10-14 is 2025-10-14T05:00Z, which still belongs to
    # 2025-10-14 whether rounded or floored; rounding must not push it forward.
    local_midnight_minus5 = DAY_1 + 5 * 3600
    transport = FakeTransport({DAY_1 + NOON: envelope()})

    result = fetch_consumption(transport, local_midnight_minus5, local_midnight_minus5)

    assert [c["data_obj"]["time"] for c in transport.calls] == [DAY_1 + NOON]
    assert [r.date for r in result.records] == ["2025-10-14"]


def test_bounds_round_to_the_nearest_utc_midnight():
    # One second before noon rounds down to that day's midnight; noon itself rounds up
    # to the next one. Start just before noon of day 1 and end at noon of day 1 gives
    # day 1 .. day 2.
    transport = FakeTransport({DAY_1 + NOON: envelope(), DAY_2 + NOON: envelope()})

    result = fetch_consumption(transport, DAY_1 + NOON - 1, DAY_1 + NOON)

    assert [c["data_obj"]["time"] for c in transport.calls] == [
        DAY_1 + NOON,
        DAY_2 + NOON,
    ]
    assert [r.epoch_s for r in result.records] == [DAY_1, DAY_2]


def test_single_instant_queries_just_its_day():
    transport = FakeTransport({DAY_1 + NOON: envelope()})

    result = fetch_consumption(transport, DAY_1 + 7, DAY_1 + 7)

    assert [c["data_obj"]["time"] for c in transport.calls] == [DAY_1 + NOON]
    assert len(result.records) == 1


def test_end_before_start_queries_nothing():
    transport = FakeTransport({})

    result = fetch_consumption(transport, DAY_2, DAY_1)

    assert transport.calls == []
    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_missing_day_is_noted_not_fabricated():
    transport = FakeTransport(
        {
            DAY_1 + NOON: envelope(1900, 100, 500),
            # A day whose result carries no usable figures at all.
            DAY_2 + NOON: {"code": 0, "result": {}},
            DAY_3 + NOON: envelope(1920, 300, 700),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_3)

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
            DAY_1 + NOON: ApiError("invalid time", code=1001),
            DAY_2 + NOON: envelope(1910, 200, 600),
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_2)

    assert [r.date for r in result.records] == ["2025-10-15"]
    assert len(result.raw) == 1  # nothing was received for the failed day
    assert len(result.notes) == 1
    assert "2025-10-14" in result.notes[0]


def test_auth_error_is_not_swallowed():
    transport = FakeTransport({DAY_1 + NOON: AuthError("rejected")})

    with pytest.raises(AuthError):
        fetch_consumption(transport, DAY_1, DAY_1)


def test_values_read_from_result_wrapper():
    # Decoy figures at the top level must be ignored; only ``result`` counts.
    payload = {
        "code": 0,
        "basal": 1.0,
        "exercise": 2.0,
        "intake": 3.0,
        "result": {"basal": 1907.5, "exercise": 365, "intake": 120},
    }
    transport = FakeTransport({DAY_1 + NOON: payload})

    result = fetch_consumption(transport, DAY_1, DAY_1)

    (record,) = result.records
    assert record.resting_kcal == 1907.5
    assert record.active_kcal == 365
    assert record.intake_kcal == 120
    assert result.raw == [{"basal": 1907.5, "exercise": 365, "intake": 120}]


def test_payload_without_result_wrapper_falls_back_to_top_level():
    transport = FakeTransport(
        {DAY_1 + NOON: {"basal": 1907.5, "exercise": 365, "intake": 0}}
    )

    result = fetch_consumption(transport, DAY_1, DAY_1)

    (record,) = result.records
    assert record.resting_kcal == 1907.5
    assert record.total_kcal == 2272.5


def test_zero_intake_is_a_value_not_missing():
    transport = FakeTransport({DAY_1 + NOON: envelope(1907.5, 0, 0)})

    result = fetch_consumption(transport, DAY_1, DAY_1)

    (record,) = result.records
    assert record.intake_kcal == 0
    assert record.active_kcal == 0
    assert record.total_kcal == 1907.5
    assert result.notes == []


def test_total_is_none_unless_resting_and_active_are_both_present():
    transport = FakeTransport(
        {
            DAY_1 + NOON: {"code": 0, "result": {"basal": 1907.5, "intake": 250}},
            DAY_2 + NOON: {"code": 0, "result": {"exercise": 365}},
        }
    )

    result = fetch_consumption(transport, DAY_1, DAY_2)

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
    transport = FakeTransport({DAY_1 + NOON: {"code": 0, "result": bad}})

    result = fetch_consumption(transport, DAY_1, DAY_1)

    assert result.records == []
    assert len(result.notes) == 1
    assert len(result.raw) == 1


def test_non_object_result_is_noted_and_skipped():
    transport = FakeTransport({DAY_1 + NOON: {"code": 0, "result": None}})

    result = fetch_consumption(transport, DAY_1, DAY_1)

    assert result.records == []
    assert result.raw == []
    assert len(result.notes) == 1
