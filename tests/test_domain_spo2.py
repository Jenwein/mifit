"""Raw SpO2 domain tests. A fake ``Transport`` serves synthetic rows; nothing here
touches the network and no real health data is used.

Rows follow the live-verified shape of raw ``spo2`` (notes/11 §1.2): the outer
object ``{key, sid, time, update_time, value, zone_name, zone_offset}`` whose
``time`` is the sample's instant, and a ``value`` JSON string holding
``{"time", "spo2"}``. The fake replaces ``get_paged`` (which already unwraps the
``result`` envelope) and returns the inner entry list.
"""

import json

from mifit.domains.base import REGISTRY
from mifit.domains.spo2 import fetch_spo2_window
from mifit.models.spo2 import Spo2Point
from mifit.transport import Transport

START = 1760371200  # 2025-10-13T16:00:00Z, a local midnight at UTC+8
END = START + 86399
ENDPOINT = "data/get_fitness_data_by_time"
ABSENT = object()
"""Stands for an outer ``time`` key that is not there at all."""


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the rows of a canned payload and records the call."""

    def __init__(self, payload):
        # No Session/http needed: ``get_paged`` is fully replaced below.
        self._payload = payload
        self.calls = []

    def get_paged(
        self,
        endpoint_path,
        data_obj,
        *,
        list_key="data_list",
        sign_path=None,
        max_pages=None,
    ):
        self.calls.append(
            {
                "endpoint_path": endpoint_path,
                "data_obj": dict(data_obj),
                "list_key": list_key,
            }
        )
        return list(self._payload["result"].get(list_key) or [])


def payload_with_rows(*rows):
    """A decrypted-envelope payload built from ``(outer_time, value)`` pairs.

    A dict ``value`` is encoded to the JSON string the wire carries; a ``str`` is
    used as the wire string itself, so a test can send a value that is not JSON.
    An ``outer_time`` of ``ABSENT`` leaves the row's ``time`` key out.
    """
    data_list = []
    for outer_time, value in rows:
        row = {
            "key": "spo2",
            "sid": "synthetic-sid",
            "update_time": 1760500000,
            "value": value if isinstance(value, str) else json.dumps(value),
            "zone_name": "Asia/Shanghai",
            "zone_offset": 28800,
        }
        if outer_time is not ABSENT:
            row["time"] = outer_time
        data_list.append(row)
    return {"code": 0, "result": {"data_list": data_list, "has_more": False, "next_key": None}}


def test_spo2_domain_is_registered():
    assert REGISTRY["spo2"] is fetch_spo2_window


def test_spo2_window_asks_a_day_before_start():
    transport = FakeTransport(payload_with_rows())

    fetch_spo2_window(transport, START, END)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    assert call["list_key"] == "data_list"
    # Raw SpO2 drops the bucket keyed at or before start_time, as heart rate
    # does (notes/11 §1.3), so the request starts a day early and is filtered.
    assert call["data_obj"] == {
        "key": "spo2",
        "start_time": START - 86400,
        "end_time": END,
        "reverse": True,
    }


def test_spo2_window_keeps_rows_at_both_bounds():
    payload = payload_with_rows(
        (END, {"time": END, "spo2": 98}),
        (START, {"time": START, "spo2": 97}),
    )

    result = fetch_spo2_window(FakeTransport(payload), START, END)

    assert result.records == [Spo2Point(END, 98), Spo2Point(START, 97)]
    # The A-faithful side: the untouched, twice-decoded value dicts.
    assert result.raw == [{"time": END, "spo2": 98}, {"time": START, "spo2": 97}]
    assert result.notes == []


def test_spo2_window_drops_rows_outside_the_asked_window():
    payload = payload_with_rows(
        (END + 1, {"time": END + 1, "spo2": 95}),
        (START + 600, {"time": START + 600, "spo2": 96}),
        (START - 1, {"time": START - 1, "spo2": 94}),
    )

    result = fetch_spo2_window(FakeTransport(payload), START, END)

    assert result.records == [Spo2Point(epoch_s=START + 600, spo2_pct=96)]
    assert result.raw == [{"time": START + 600, "spo2": 96}]
    # The day-early margin is the SDK's own doing: its rows go without a note.
    assert result.notes == []


def test_spo2_without_usable_value_is_skipped_with_note():
    values = [
        {"time": START + 600},  # spo2 absent
        {"time": START + 1200, "spo2": 0},
        {"time": START + 1800, "spo2": "97"},
    ]
    payload = payload_with_rows(*((value["time"], value) for value in values))

    result = fetch_spo2_window(FakeTransport(payload), START, END)

    # No 0 % or string-parsed point is fabricated.
    assert result.records == []
    assert len(result.notes) == 3
    assert all("usable SpO2 value" in note for note in result.notes)
    # The untouched values of the skipped rows are still preserved in ``raw``.
    assert result.raw == values


def test_spo2_bad_value_and_bad_time_are_noted():
    payload = payload_with_rows(
        (START + 600, "not json"),
        ("x", {"time": START + 1200, "spo2": 97}),
        (START + 1800, {"time": START + 1800, "spo2": 96}),
    )

    result = fetch_spo2_window(FakeTransport(payload), START, END)

    assert result.records == [Spo2Point(epoch_s=START + 1800, spo2_pct=96)]
    assert result.raw == [{"time": START + 1800, "spo2": 96}]
    assert result.notes == [
        "Skipped an SpO2 row whose value is missing or not valid JSON.",
        "Skipped an SpO2 row without a usable time.",
    ]


def test_spo2_value_that_is_not_an_object_is_noted():
    payload = payload_with_rows((START + 600, "[97]"), (ABSENT, {"spo2": 97}))

    result = fetch_spo2_window(FakeTransport(payload), START, END)

    assert result.records == []
    assert result.raw == []
    assert result.notes == [
        "Skipped an SpO2 row whose value is not an object.",
        "Skipped an SpO2 row without a usable time.",
    ]


def test_spo2_empty_window_yields_no_records_and_no_notes():
    result = fetch_spo2_window(FakeTransport(payload_with_rows()), START, END)

    # No fabricated points for a window the service returned nothing for.
    assert result.records == []
    assert result.raw == []
    assert result.notes == []
