"""Weight domain tests. A fake ``Transport`` serves synthetic rows; nothing here
touches the network and no real health data is used.

Rows follow the live-verified shape of raw ``weight`` (notes/11 §2): the outer
object ``{key, sid, time, update_time, value, zone_name, zone_offset}`` whose
``time`` is the record's instant, and a ``value`` JSON string holding the weight,
the BMI and an all-zero body composition. The fake replaces ``get_paged`` (which
already unwraps the ``result`` envelope) and returns the inner entry list.
"""

import dataclasses
import json

from mifit.domains.base import REGISTRY
from mifit.domains.weight import fetch_weight
from mifit.models.weight import WeightRecord
from mifit.transport import Transport

T = 1600000000
START = T - 86400
END = T + 86400
ENDPOINT = "data/get_fitness_data_by_time"
ABSENT = object()
"""Stands for a key (an outer ``time``, or a value field) that is not there at all."""


def weight_value(**overrides):
    """The live-verified weight value (synthetic numbers), with overrides; a field
    set to ``ABSENT`` is left out of the dict."""
    value = {
        "time": T,
        "weight": 70.5,
        "bmi": 23.0,
        "body_fat_rate": 0.0,
        "muscle_rate": 0.0,
        "moisture_rate": 0.0,
        "bone_mass": 0.0,
        "protein_rate": 0.0,
        "visceral_fat": 0.0,
        "basal_metabolism": 0,
    }
    value.update(overrides)
    return {k: v for k, v in value.items() if v is not ABSENT}


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
            "key": "weight",
            "sid": "synthetic-sid",
            "update_time": T + 60,
            "value": value if isinstance(value, str) else json.dumps(value),
            "zone_name": "Asia/Shanghai",
            "zone_offset": 28800,
        }
        if outer_time is not ABSENT:
            row["time"] = outer_time
        data_list.append(row)
    return {"code": 0, "result": {"data_list": data_list, "has_more": False, "next_key": None}}


def rows_of(*values):
    """A payload with one row per value, each timed by its own ``time`` field."""
    return payload_with_rows(*((value["time"], value) for value in values))


def test_weight_domain_is_registered():
    assert REGISTRY["weight"] is fetch_weight


def test_weight_asks_a_day_before_start():
    transport = FakeTransport(payload_with_rows())

    fetch_weight(transport, START, END)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    assert call["list_key"] == "data_list"
    assert call["data_obj"] == {
        "key": "weight",
        "start_time": START - 86400,
        "end_time": END,
        "reverse": True,
    }


def test_maps_weight_and_bmi():
    payload = payload_with_rows((T, weight_value()))

    result = fetch_weight(FakeTransport(payload), START, END)

    assert result.records == [WeightRecord(epoch_s=T, weight_kg=70.5, bmi=23.0)]
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [weight_value()]
    assert result.notes == []


def test_int_weight_becomes_float():
    payload = payload_with_rows((T, weight_value(weight=70)))

    result = fetch_weight(FakeTransport(payload), START, END)

    (record,) = result.records
    assert record.weight_kg == 70.0
    assert isinstance(record.weight_kg, float)


def test_row_without_usable_weight_is_skipped_with_note():
    values = [
        weight_value(time=T + 60, weight=ABSENT),
        weight_value(time=T + 120, weight=0),
        weight_value(time=T + 180, weight="70"),
    ]

    result = fetch_weight(FakeTransport(rows_of(*values)), START, END)

    # No 0 kg or string-parsed record is fabricated.
    assert result.records == []
    assert len(result.notes) == 3
    assert all("usable weight" in note for note in result.notes)
    # The untouched values of the skipped rows are still preserved in ``raw``.
    assert result.raw == values


def test_negative_bool_or_non_finite_weight_is_skipped():
    values = [
        weight_value(time=T + 60, weight=-70.5),
        weight_value(time=T + 120, weight=True),
        # ``json`` writes and reads back ``Infinity``, as a hostile payload would.
        weight_value(time=T + 180, weight=float("inf")),
    ]

    result = fetch_weight(FakeTransport(rows_of(*values)), START, END)

    assert result.records == []
    assert len(result.notes) == 3
    assert all("usable weight" in note for note in result.notes)


def test_missing_or_zero_bmi_becomes_none():
    values = [
        weight_value(time=T + 60, bmi=ABSENT),
        weight_value(time=T + 120, bmi=0),
        weight_value(time=T + 180, bmi="23"),
        weight_value(time=T + 240, bmi=-1.0),
    ]

    result = fetch_weight(FakeTransport(rows_of(*values)), START, END)

    # The weight is real, so each row keeps its record; only the BMI is None.
    assert [r.weight_kg for r in result.records] == [70.5] * 4
    assert [r.bmi for r in result.records] == [None] * 4
    assert result.notes == []


def test_composition_stays_out_of_the_record():
    payload = payload_with_rows((T, weight_value()))

    result = fetch_weight(FakeTransport(payload), START, END)

    # Body composition is unverified (all zero on the account checked): A only.
    assert {f.name for f in dataclasses.fields(WeightRecord)} == {
        "epoch_s",
        "weight_kg",
        "bmi",
    }
    assert result.raw[0]["body_fat_rate"] == 0.0


def test_rows_outside_the_window_are_dropped():
    payload = payload_with_rows(
        (START - 1, weight_value(time=START - 1)),
        (END + 1, weight_value(time=END + 1)),
    )

    result = fetch_weight(FakeTransport(payload), START, END)

    assert result.records == []
    assert result.raw == []
    # The day-early margin is the SDK's own doing: its rows go without a note.
    assert result.notes == []


def test_rows_at_both_bounds_are_kept():
    payload = payload_with_rows(
        (END, weight_value(time=END, weight=71.0)),
        (START, weight_value(time=START, weight=70.0)),
    )

    result = fetch_weight(FakeTransport(payload), START, END)

    assert [(r.epoch_s, r.weight_kg) for r in result.records] == [(END, 71.0), (START, 70.0)]


def test_record_is_timed_by_the_outer_time():
    payload = payload_with_rows((T, weight_value(time=T + 5)))

    result = fetch_weight(FakeTransport(payload), START, END)

    assert [r.epoch_s for r in result.records] == [T]


def test_bad_value_and_bad_time_are_noted():
    payload = payload_with_rows(
        (T + 60, "not json"),
        ("x", weight_value(time=T + 120)),
        (T + 180, "[70.5]"),
        (T + 240, weight_value(time=T + 240)),
    )

    result = fetch_weight(FakeTransport(payload), START, END)

    assert result.records == [WeightRecord(epoch_s=T + 240, weight_kg=70.5, bmi=23.0)]
    assert result.raw == [weight_value(time=T + 240)]
    assert result.notes == [
        "Skipped a weight row whose value is missing or not valid JSON.",
        "Skipped a weight row whose value is not an object.",
        "Skipped a weight row without a usable time.",
    ]


def test_empty_range_yields_no_records_and_no_notes():
    result = fetch_weight(FakeTransport(payload_with_rows()), START, END)

    # No fabricated record for a range the service returned nothing for.
    assert result.records == []
    assert result.raw == []
    assert result.notes == []
