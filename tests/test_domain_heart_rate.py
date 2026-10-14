"""Heart-rate domain tests. ``PagedFake`` serves a synthetic payload; nothing
here touches the network and no real health data is used.

The fixture follows the same decrypted envelope as the steps fixture: rows live
under ``result.data_list`` and carry their payload as a JSON string in ``value``
(``{"time", "bpm", "type"}``). ``PagedFake`` serves the inner entry list, as the
real paged read does once it has unwrapped ``result``.
"""

import json
from pathlib import Path

from mifit.domains.base import REGISTRY, stalled_note
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.models.heart_rate import HeartRatePoint
from support import ABSENT, PagedFake, payload_with_rows_from

FIXTURE = Path(__file__).parent / "fixtures" / "hr_raw_sample.json"

START = 1759248000
END = 1759334399
ENDPOINT = "data/get_fitness_data_by_time"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


payload_with_rows = payload_with_rows_from(load_fixture()["result"]["data_list"][0])


def payload_with_values(*values):
    """A fixture-shaped payload whose rows carry the given value dicts."""
    payload = load_fixture()
    template = payload["result"]["data_list"][0]
    payload["result"]["data_list"] = [
        {**template, "value": json.dumps(value)} for value in values
    ]
    return payload


def test_parses_hr_point():
    transport = PagedFake(load_fixture())

    result = fetch_heart_rate_window(transport, START, END)

    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]
    # bpm is the wire value as is: the x5 factor is a logging artifact, not wire.
    assert result.records[0].bpm == 72
    # The A-faithful side: the untouched, twice-decoded value dict.
    assert result.raw == [{"time": 1759248600, "bpm": 72, "type": None}]


def test_window_result_has_no_incompleteness_note():
    transport = PagedFake(load_fixture())

    result = fetch_heart_rate_window(transport, START, END)

    # The read asks a day early and filters, so a clean window carries no caveat.
    assert result.notes == []


def test_window_asks_a_day_before_start():
    transport = PagedFake(load_fixture())

    fetch_heart_rate_window(transport, START, END)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == ENDPOINT
    assert call["list_key"] == "data_list"
    # The service drops a bucket keyed at or before start_time (notes/08), so
    # the request starts a day early and the rows are filtered afterwards.
    assert call["data_obj"] == {
        "key": "heart_rate",
        "start_time": START - 86400,
        "end_time": END,
        "reverse": True,
    }


def test_window_drops_rows_outside_the_asked_window():
    payload = payload_with_rows(
        (END + 1, {"time": END + 1, "bpm": 61, "type": 0}),
        (START + 600, {"time": START + 600, "bpm": 62, "type": 0}),
        (START - 1, {"time": START - 1, "bpm": 63, "type": 0}),
    )

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert result.records == [HeartRatePoint(epoch_s=START + 600, bpm=62, kind=0)]
    assert result.raw == [{"time": START + 600, "bpm": 62, "type": 0}]
    # The day-early margin is the SDK's own doing: its rows go without a note.
    assert result.notes == []


def test_window_keeps_rows_at_both_bounds():
    payload = payload_with_rows(
        (END, {"time": END, "bpm": 64, "type": 0}),
        (START, {"time": START, "bpm": 65, "type": 0}),
    )

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert result.records == [
        HeartRatePoint(epoch_s=END, bpm=64, kind=0),
        HeartRatePoint(epoch_s=START, bpm=65, kind=0),
    ]
    assert result.notes == []


def test_row_without_usable_time_is_skipped_with_note():
    payload = payload_with_rows(
        (ABSENT, {"time": START + 600, "bpm": 66, "type": 0}),
        ("x", {"time": START + 1200, "bpm": 67, "type": 0}),
    )

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert result.records == []
    assert result.raw == []
    assert len(result.notes) == 2
    assert all("usable time" in note for note in result.notes)


def test_full_read_omits_start_time():
    transport = PagedFake(load_fixture())

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
    transport = PagedFake(load_fixture())

    fetch_heart_rate_full(transport, END)

    assert transport.calls[0]["max_pages"] == 10


def test_kind_is_kept_raw_and_missing_type_is_none():
    payload = payload_with_values(
        {"time": 1759248600, "bpm": 70, "type": 3},
        {"time": 1759248660, "bpm": 71, "type": 0},
        {"time": 1759248720, "bpm": 72},  # type absent
    )

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert [(p.epoch_s, p.bpm, p.kind) for p in result.records] == [
        (1759248600, 70, 3),
        (1759248660, 71, 0),
        (1759248720, 72, None),
    ]


def test_empty_window_yields_no_records():
    payload = load_fixture()
    payload["result"]["data_list"] = []

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    # No fabricated points for a window the service returned nothing for.
    assert result.records == []
    assert result.raw == []
    assert result.notes == []


def test_entry_without_bpm_is_skipped_not_zeroed():
    payload = payload_with_values(
        {"time": 1759248600, "type": 1},  # no bpm
        {"time": 1759248660, "bpm": None},  # null bpm
        {"time": 1759248720, "bpm": 65, "type": None},
    )

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert [p.bpm for p in result.records] == [65]
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

    result = fetch_heart_rate_window(PagedFake(payload), START, END)

    assert [p.bpm for p in result.records] == [72]
    assert len([n for n in result.notes if "skipped" in n.lower()]) == 3
    assert result.raw == [{"time": 1759248600, "bpm": 72, "type": None}]


def test_capped_full_read_says_so():
    transport = PagedFake(load_fixture(), truncated=True)

    result = fetch_heart_rate_full(transport, END)

    assert len(result.notes) == 1
    assert "10-page cap" in result.notes[0]
    # The points that were read are still returned.
    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]


def test_capped_full_read_names_the_cap_it_was_given():
    transport = PagedFake(load_fixture(), truncated=True)

    result = fetch_heart_rate_full(transport, END, max_pages=3)

    assert len(result.notes) == 1
    assert "3-page cap" in result.notes[0]


def test_uncapped_full_read_has_no_cap_note():
    transport = PagedFake(load_fixture(), truncated=False)

    result = fetch_heart_rate_full(transport, END)

    assert result.notes == []


def test_window_domain_is_registered():
    assert REGISTRY["heart_rate"] is fetch_heart_rate_window


def test_full_read_is_not_registered_under_the_window_contract():
    # The registry contract is fn(transport, start_epoch_s, end_epoch_s); the
    # full read takes (transport, end_epoch_s, max_pages), so registering it
    # would mis-dispatch the end time as the page cap.
    assert fetch_heart_rate_full not in REGISTRY.values()


def test_stalled_read_says_so():
    payload = payload_with_rows(
        (ABSENT, {"time": START + 600, "bpm": 66, "type": 0}),
        (START + 1200, {"time": START + 1200, "bpm": 67, "type": 0}),
    )

    result = fetch_heart_rate_window(PagedFake(payload, stalled=True), START, END)

    # After the unusable row's note, and the rows that were read are still returned.
    assert len(result.notes) == 2
    assert result.notes[-1] == stalled_note("heart-rate rows")
    assert result.records == [HeartRatePoint(epoch_s=START + 1200, bpm=67, kind=0)]


def test_stalled_full_read_says_so():
    result = fetch_heart_rate_full(PagedFake(load_fixture(), stalled=True), END)

    assert result.notes == [stalled_note("heart-rate rows")]
    assert result.records == [HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)]
