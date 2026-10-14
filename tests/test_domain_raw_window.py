"""Shared raw-window read tests. ``PagedFake`` serves synthetic rows; nothing here
touches the network and no real health data is used.

``fetch_raw_window`` asks the fitness-data endpoint for a day more before the
start (the service drops the bucket keyed at or before ``start_time``, notes/08
§2.3, notes/11 §1.3) and keeps only the rows whose outer ``time`` lies in the
caller's window. ``PagedFake`` serves the given entries as they are, as the real
paged read does once it has unwrapped the ``result`` envelope.
"""

from unittest import mock

from mifit.domains.raw_window import (
    RAW_ENDPOINT,
    START_MARGIN_S,
    RawWindow,
    fetch_raw_window,
)
from support import PagedFake

START = 1760371200  # 2025-10-13T16:00:00Z, a local midnight at UTC+8
END = START + 86399


def served(entries, **options):
    """A ``PagedFake`` whose paged read serves ``entries`` as they are."""
    return PagedFake({"result": {"data_list": entries}}, **options)


def row(outer_time):
    """A synthetic raw row (live outer shape) whose outer ``time`` is given."""
    return {
        "key": "weight",
        "sid": "synthetic-sid",
        "time": outer_time,
        "update_time": 1760500000,
        "value": '{"time": 1760400000}',
        "zone_name": "Asia/Shanghai",
        "zone_offset": 28800,
    }


def test_raw_window_asks_a_day_before_start():
    transport = served([])

    with mock.patch.object(
        transport, "get_paged_result", wraps=transport.get_paged_result
    ) as spy:
        fetch_raw_window(transport, "weight", START, END)

    assert RAW_ENDPOINT == "data/get_fitness_data_by_time"
    assert START_MARGIN_S == 86400
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == RAW_ENDPOINT
    assert call["list_key"] == "data_list"
    assert spy.call_args.kwargs.get("sign_path") is None
    assert call["max_pages"] is None
    assert call["data_obj"] == {
        "key": "weight",
        "start_time": START - 86400,
        "end_time": END,
        "reverse": True,
    }


def test_raw_window_keeps_bounds_and_drops_the_rest():
    before, at_start, at_end, after = row(START - 1), row(START), row(END), row(END + 1)
    # Newest first, as the service sends with ``reverse``.
    transport = served([after, at_end, at_start, before])

    kept, unusable, stalled = fetch_raw_window(transport, "weight", START, END)

    assert len(kept) == 2
    assert kept[0] is at_end
    assert kept[1] is at_start
    # Untouched: still the wire row, ``value`` still a JSON string.
    assert kept[1] == row(START)
    # The margin and the rows after the end are the SDK's own doing: not counted.
    assert unusable == 0
    assert stalled is False


def test_raw_window_counts_rows_without_usable_time():
    no_time = {k: v for k, v in row(START).items() if k != "time"}
    entries = [no_time, row("x"), row(True), ["not", "a", "row"]]

    kept, unusable, _ = fetch_raw_window(served(entries), "weight", START, END)

    assert kept == []
    assert unusable == 4


def test_raw_window_reports_a_stalled_read():
    window = fetch_raw_window(served([row(START)], stalled=True), "weight", START, END)

    assert isinstance(window, RawWindow)
    assert window.kept == [row(START)]
    assert window.unusable == 0
    assert window.stalled is True
