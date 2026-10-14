"""Steps domain tests. A fake ``Transport`` serves a synthetic payload; nothing
here touches the network and no real health data is used.

The fixture follows the real decrypted envelope documented in
mi-health-lab ``docs/cloud-read-06.md``: rows live under ``result.data_list``
(with ``has_more``/``next_key`` beside them) and carry
``tag/key/time/value/sid/source_sid_list/update_time/watermark``. The real daily
row's ``time`` is the UTC-midnight day label (1759276800 = 2025-10-01T00:00Z).
"""

import json
from pathlib import Path

from mifit.domains.base import REGISTRY
from mifit.domains.steps import fetch_steps
from mifit.models.steps import StepRecord
from mifit.transport import Transport

FIXTURE = Path(__file__).parent / "fixtures" / "steps_daily_sample.json"

START = 1759276800
END = 1759363199
ENDPOINT = "data/get_aggregated_fitness_data_by_time"


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the rows of a canned payload and records the call.

    The fake reads ``result.data_list`` (the real envelope). Which layer unwraps
    ``result`` for the real ``get_paged`` is outside this domain's concern.
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


def payload_with_values(*values):
    """A fixture-shaped payload whose rows carry the given value dicts."""
    payload = load_fixture()
    template = payload["result"]["data_list"][0]
    payload["result"]["data_list"] = [
        {**template, "value": json.dumps(value)} for value in values
    ]
    return payload


def test_parses_steps_record():
    transport = FakeTransport(load_fixture())

    result = fetch_steps(transport, START, END)

    assert result.records == [
        StepRecord(
            date="2025-10-01",
            steps=8000,
            distance_m=5200,
            calories=210,
            epoch_s=1759276800,
        )
    ]
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [
        {"time": 1759276800, "steps": 8000, "distance": 5200, "calories": 210}
    ]
    assert result.notes == []


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
    payload = payload_with_values(
        {"time": 1759276800, "steps": 100},  # distance/calories absent
        {"time": 1759363200, "steps": 200, "distance": None, "calories": None},
    )

    result = fetch_steps(FakeTransport(payload), START, END)

    assert [r.steps for r in result.records] == [100, 200]
    for record in result.records:
        assert record.distance_m is None
        assert record.calories is None


def test_explicit_zero_steps_is_kept():
    payload = payload_with_values({"time": 1759276800, "steps": 0, "distance": 0})

    result = fetch_steps(FakeTransport(payload), START, END)

    # A reported zero is data; only an absent value must not become zero.
    assert [(r.steps, r.distance_m, r.calories) for r in result.records] == [
        (0, 0, None)
    ]


def test_entry_without_steps_is_skipped_not_zeroed():
    payload = payload_with_values(
        {"time": 1759276800, "distance": 5200},  # no steps
        {"time": 1759363200, "steps": 200},
    )

    result = fetch_steps(FakeTransport(payload), START, END)

    assert [r.steps for r in result.records] == [200]
    assert len(result.notes) == 1
    # The untouched value of the skipped row is still preserved in ``raw``.
    assert result.raw == [
        {"time": 1759276800, "distance": 5200},
        {"time": 1759363200, "steps": 200},
    ]


def test_steps_domain_is_registered():
    assert REGISTRY["steps"] is fetch_steps
