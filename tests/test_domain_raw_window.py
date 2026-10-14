"""Shared raw-window read tests. A fake ``Transport`` serves synthetic rows;
nothing here touches the network and no real health data is used.

``fetch_raw_window`` asks the fitness-data endpoint for a day more before the
start (the service drops the bucket keyed at or before ``start_time``, notes/08
§2.3, notes/11 §1.3) and keeps only the rows whose outer ``time`` lies in the
caller's window. The fake replaces ``get_paged`` (which already unwraps the
``result`` envelope) and returns the given entries as they are.
"""

from mifit.domains.raw_window import RAW_ENDPOINT, START_MARGIN_S, fetch_raw_window
from mifit.transport import Transport

START = 1760371200  # 2025-10-13T16:00:00Z, a local midnight at UTC+8
END = START + 86399


class FakeTransport(Transport):
    """Replaces ``get_paged`` with the given entries and records the call."""

    def __init__(self, entries):
        # No Session/http needed: ``get_paged`` is fully replaced below.
        self._entries = entries
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
                "sign_path": sign_path,
                "max_pages": max_pages,
            }
        )
        return list(self._entries)


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
    transport = FakeTransport([])

    fetch_raw_window(transport, "weight", START, END)

    assert RAW_ENDPOINT == "data/get_fitness_data_by_time"
    assert START_MARGIN_S == 86400
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["endpoint_path"] == RAW_ENDPOINT
    assert call["list_key"] == "data_list"
    assert call["sign_path"] is None
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
    transport = FakeTransport([after, at_end, at_start, before])

    kept, unusable = fetch_raw_window(transport, "weight", START, END)

    assert len(kept) == 2
    assert kept[0] is at_end
    assert kept[1] is at_start
    # Untouched: still the wire row, ``value`` still a JSON string.
    assert kept[1] == row(START)
    # The margin and the rows after the end are the SDK's own doing: not counted.
    assert unusable == 0


def test_raw_window_counts_rows_without_usable_time():
    no_time = {k: v for k, v in row(START).items() if k != "time"}
    entries = [no_time, row("x"), row(True), ["not", "a", "row"]]

    kept, unusable = fetch_raw_window(FakeTransport(entries), "weight", START, END)

    assert kept == []
    assert unusable == 4
