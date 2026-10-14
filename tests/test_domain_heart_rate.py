"""Heart-rate domain tests. A fake ``Transport`` serves a synthetic payload;
nothing here touches the network and no real health data is used.

The fixture follows the same decrypted envelope as the steps fixture: rows live
under ``result.data_list`` and carry their payload as a JSON string in ``value``
(``{"time", "bpm", "type"}``). The fake replaces ``get_paged`` (which already
unwraps ``result``) and returns the inner entry list.
"""

import json
from pathlib import Path

from mifit.domains.base import REGISTRY
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.models.heart_rate import HeartRatePoint
from mifit.transport import PagedResult, Transport

FIXTURE = Path(__file__).parent / "fixtures" / "hr_raw_sample.json"

START = 1759248000
END = 1759334399
ENDPOINT = "data/get_fitness_data_by_time"


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the rows of a canned payload and records the call.

    ``get_paged_result`` (what the full read uses) serves the same rows through
    ``get_paged``, so it records the call identically, and reports ``truncated``.
    """

    def __init__(self, payload, truncated=False):
        # No Session/http needed: ``get_paged`` is fully replaced below.
        self._payload = payload
        self._truncated = truncated
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
                "max_pages": max_pages,
            }
        )
        return list(self._payload["result"].get(list_key) or [])

    def get_paged_result(
        self,
        endpoint_path,
        data_obj,
        *,
        list_key="data_list",
        sign_path=None,
        max_pages=None,
    ):
        items = self.get_paged(
            endpoint_path,
            data_obj,
            list_key=list_key,
            sign_path=sign_path,
            max_pages=max_pages,
        )
        return PagedResult(items, self._truncated)


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


def test_parses_hr_point():
    transport = FakeTransport(load_fixture())

    result = fetch_heart_rate_window(transport, START, END)

    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]
    # bpm is the wire value as is: the x5 factor is a logging artifact, not wire.
    assert result.records[0].bpm == 72
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [{"time": 1759248600, "bpm": 72, "type": None}]


def test_window_result_flags_possible_incompleteness():
    transport = FakeTransport(load_fixture())

    result = fetch_heart_rate_window(transport, START, END)

    assert result.notes
    assert all(isinstance(note, str) and note.strip() for note in result.notes)
    assert any("incomplete" in note.lower() for note in result.notes)


def test_window_requests_heart_rate_for_the_window():
    transport = FakeTransport(load_fixture())

    fetch_heart_rate_window(transport, START, END)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    assert call["list_key"] == "data_list"
    assert call["data_obj"] == {
        "key": "heart_rate",
        "start_time": START,
        "end_time": END,
        "reverse": True,
    }


def test_full_read_omits_start_time():
    transport = FakeTransport(load_fixture())

    result = fetch_heart_rate_full(transport, END, max_pages=7)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    assert "start_time" not in call["data_obj"]
    assert call["data_obj"] == {"key": "heart_rate", "end_time": END, "reverse": True}
    assert call["list_key"] == "data_list"
    assert call["max_pages"] == 7
    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]


def test_full_read_defaults_to_ten_pages():
    transport = FakeTransport(load_fixture())

    fetch_heart_rate_full(transport, END)

    assert transport.calls[0]["max_pages"] == 10


def test_kind_is_kept_raw_and_missing_type_is_none():
    payload = payload_with_values(
        {"time": 1759248600, "bpm": 70, "type": 3},
        {"time": 1759248660, "bpm": 71, "type": 0},
        {"time": 1759248720, "bpm": 72},  # type absent
    )

    result = fetch_heart_rate_window(FakeTransport(payload), START, END)

    assert [(p.epoch_s, p.bpm, p.kind) for p in result.records] == [
        (1759248600, 70, 3),
        (1759248660, 71, 0),
        (1759248720, 72, None),
    ]


def test_empty_window_yields_no_records():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_heart_rate_window(FakeTransport(payload), START, END)

    # No fabricated points for a window the service returned nothing for.
    assert result.records == []
    assert result.raw == []
    # An empty windowed answer is exactly where incompleteness matters.
    assert result.notes


def test_entry_without_bpm_is_skipped_not_zeroed():
    payload = payload_with_values(
        {"time": 1759248600, "type": 1},  # no bpm
        {"time": 1759248660, "bpm": None},  # null bpm
        {"time": 1759248720, "bpm": 65, "type": None},
    )

    result = fetch_heart_rate_window(FakeTransport(payload), START, END)

    assert [p.bpm for p in result.records] == [65]
    # Two skip notes beside the incompleteness note.
    assert len([n for n in result.notes if "skipped" in n.lower()]) == 2
    # The untouched values of the skipped rows are still preserved in ``raw``.
    assert result.raw == [
        {"time": 1759248600, "type": 1},
        {"time": 1759248660, "bpm": None},
        {"time": 1759248720, "bpm": 65, "type": None},
    ]


def test_unparseable_value_is_skipped_with_note():
    payload = load_fixture()
    template = payload["result"]["data_list"][0]
    payload["result"]["data_list"] = [
        {**template, "value": "not json"},
        {k: v for k, v in template.items() if k != "value"},
        {**template, "value": "[1, 2]"},
        template,
    ]

    result = fetch_heart_rate_window(FakeTransport(payload), START, END)

    assert [p.bpm for p in result.records] == [72]
    assert len([n for n in result.notes if "skipped" in n.lower()]) == 3
    assert result.raw == [{"time": 1759248600, "bpm": 72, "type": None}]


def test_full_read_does_not_carry_the_windowed_query_caveat():
    transport = FakeTransport(load_fixture())

    result = fetch_heart_rate_full(transport, END)

    # The full read is the candidate fuller read, not the windowed query the
    # incompleteness caveat is about.
    assert not any("windowed" in note.lower() for note in result.notes)


def test_capped_full_read_says_so():
    transport = FakeTransport(load_fixture(), truncated=True)

    result = fetch_heart_rate_full(transport, END)

    assert len(result.notes) == 1
    assert "10-page cap" in result.notes[0]
    # The points that were read are still returned.
    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]


def test_capped_full_read_names_the_cap_it_was_given():
    transport = FakeTransport(load_fixture(), truncated=True)

    result = fetch_heart_rate_full(transport, END, max_pages=3)

    assert len(result.notes) == 1
    assert "3-page cap" in result.notes[0]


def test_uncapped_full_read_has_no_cap_note():
    transport = FakeTransport(load_fixture(), truncated=False)

    result = fetch_heart_rate_full(transport, END)

    assert result.notes == []


def test_window_domain_is_registered():
    assert REGISTRY["heart_rate"] is fetch_heart_rate_window


def test_full_read_is_not_registered_under_the_window_contract():
    # The registry contract is fn(transport, start_epoch_s, end_epoch_s); the
    # full read takes (transport, end_epoch_s, max_pages), so registering it
    # would mis-dispatch the end time as the page cap.
    assert fetch_heart_rate_full not in REGISTRY.values()
