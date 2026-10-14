"""Steps domain tests. A fake ``Transport`` serves a synthetic payload; nothing
here touches the network and no real health data is used.

The fixture follows the live-verified shape of the decrypted envelope: rows live
under ``result.data_list`` (with ``has_more``/``next_key`` beside them). Each row
is the OUTER object ``{sid, tag, key, time, value, update_time, watermark}``:
``time`` is the UTC-midnight day label (1760400000 = 2025-10-14T00:00Z) and
``value`` is a JSON string holding only ``{steps, distance, calories}`` -- the
decoded value has no ``time`` of its own. See also mi-health-lab
``docs/cloud-read-06.md``.
"""

import dataclasses
import json
from pathlib import Path

from mifit.domains.base import REGISTRY
from mifit.domains.steps import fetch_steps
from mifit.models.steps import StepRecord
from mifit.transport import Transport

FIXTURE = Path(__file__).parent / "fixtures" / "steps_daily_sample.json"

DAY_1 = 1760400000  # 2025-10-14T00:00:00Z
DAY_2 = DAY_1 + 86400
START = DAY_1
END = DAY_2 + 86399
ENDPOINT = "data/get_aggregated_fitness_data_by_time"


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the rows of a canned payload and records the call.

    The fake reads ``result.data_list`` (the real envelope) and returns the outer
    row objects, as the real ``get_paged`` does.
    """

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


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def payload_with_rows(*rows):
    """A fixture-shaped payload built from ``(outer_time, value_dict)`` pairs."""
    payload = load_fixture()
    template = payload["result"]["data_list"][0]
    payload["result"]["data_list"] = [
        {**template, "time": time, "value": json.dumps(value)} for time, value in rows
    ]
    return payload


def test_parses_steps_record():
    transport = FakeTransport(load_fixture())

    result = fetch_steps(transport, START, END)

    assert result.records == [
        StepRecord(
            date="2025-10-14",
            steps=8000,
            distance_m=5200,
            step_calories=210,
            epoch_s=1760400000,
        )
    ]
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [{"steps": 8000, "distance": 5200, "calories": 210}]
    assert result.notes == []


def test_timestamp_comes_from_the_outer_entry():
    # The decoded value carries no ``time``; the day is the outer entry's.
    payload = payload_with_rows((DAY_2, {"steps": 1}))

    result = fetch_steps(FakeTransport(payload), START, END)

    assert [(r.epoch_s, r.date) for r in result.records] == [(DAY_2, "2025-10-15")]


def test_requests_daily_report_steps_for_the_window():
    transport = FakeTransport(load_fixture())

    fetch_steps(transport, START, END)

    assert transport.calls == [
        {
            "endpoint_path": ENDPOINT,
            "data_obj": {
                "tag": "daily_report",
                "key": "steps",
                "reverse": True,
                "limit": 50,
                "start_time": START,
                "end_time": END,
            },
            "list_key": "data_list",
        }
    ]


def test_empty_day_yields_no_records():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_steps(FakeTransport(payload), START, END)

    # No fabricated zero for a day the service returned nothing for.
    assert result.records == []
    assert result.raw == []


def test_null_fields_stay_none():
    payload = payload_with_rows(
        (DAY_1, {"steps": 100}),  # distance/calories absent
        (DAY_2, {"steps": 200, "distance": None, "calories": None}),
    )

    result = fetch_steps(FakeTransport(payload), START, END)

    assert [r.steps for r in result.records] == [100, 200]
    for record in result.records:
        assert record.distance_m is None
        assert record.step_calories is None


def test_explicit_zero_steps_is_kept():
    payload = payload_with_rows((DAY_1, {"steps": 0, "distance": 0}))

    result = fetch_steps(FakeTransport(payload), START, END)

    # A reported zero is data; only an absent value must not become zero.
    assert [(r.steps, r.distance_m, r.step_calories) for r in result.records] == [
        (0, 0, None)
    ]


def test_entry_without_steps_is_skipped_not_zeroed():
    payload = payload_with_rows(
        (DAY_1, {"distance": 5200}),  # no steps
        (DAY_2, {"steps": 200}),
    )

    result = fetch_steps(FakeTransport(payload), START, END)

    assert [r.steps for r in result.records] == [200]
    assert len(result.notes) == 1
    # The untouched value of the skipped row is still preserved in ``raw``.
    assert result.raw == [{"distance": 5200}, {"steps": 200}]


def test_entry_without_a_usable_outer_time_is_skipped():
    payload = payload_with_rows((DAY_1, {"steps": 100}), (DAY_2, {"steps": 200}))
    rows = payload["result"]["data_list"]
    del rows[0]["time"]
    rows[1]["time"] = None

    result = fetch_steps(FakeTransport(payload), START, END)

    assert result.records == []
    assert len(result.notes) == 2
    assert result.raw == [{"steps": 100}, {"steps": 200}]


def test_walking_calories_land_in_step_calories_not_calories():
    # The step report's own ``calories`` (walking calories) is exposed under the
    # explicit name ``step_calories`` so it is not mistaken for the app's calorie
    # ring (that figure comes from the consumption domain).
    payload = payload_with_rows((DAY_1, {"steps": 8000, "calories": 210}))

    (record,) = fetch_steps(FakeTransport(payload), START, END).records

    assert record.step_calories == 210
    assert "step_calories" in {f.name for f in dataclasses.fields(StepRecord)}
    assert "calories" not in {f.name for f in dataclasses.fields(StepRecord)}
    assert not hasattr(record, "calories")


def test_steps_domain_is_registered():
    assert REGISTRY["steps"] is fetch_steps
