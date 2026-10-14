"""Pagination tests for ``Transport.get_paged``. A ``Transport`` subclass with a
scripted ``get_json`` stands in for the service; nothing here touches the network
and all payloads are synthetic."""

import copy

from mifit.transport import Transport

ENDPOINT = "data/get_fitness_data_by_time"
SIGN_PATH = "/app/v1/data/get_fitness_data_by_time"
BASE_REQUEST = {"sid": "synthetic", "start_time": 1, "end_time": 2}


class ScriptedTransport(Transport):
    """Answers each ``get_json`` call with the next scripted payload and records a
    snapshot of every request (so later mutation of a dict cannot hide a bug)."""

    def __init__(self, pages):
        # No Session/http needed: ``get_json`` is fully replaced below.
        self._pages = list(pages)
        self.calls = []

    def get_json(self, endpoint_path, data_obj, *, sign_path=None):
        self.calls.append(
            {
                "endpoint_path": endpoint_path,
                "data_obj": copy.deepcopy(data_obj),
                "sign_path": sign_path,
            }
        )
        if not self._pages:
            raise AssertionError("get_paged requested more pages than were scripted")
        return self._pages.pop(0)


def page(items, *, has_more=False, next_key=None):
    payload = {"code": 0, "data_list": items, "has_more": has_more}
    if next_key is not None:
        payload["next_key"] = next_key
    return payload


def test_collects_all_pages_until_has_more_false():
    transport = ScriptedTransport(
        [
            page([{"n": 1}, {"n": 2}], has_more=True, next_key="cursor-1"),
            page([{"n": 3}], has_more=True, next_key="cursor-2"),
            page([{"n": 4}, {"n": 5}], has_more=False),
        ]
    )

    result = transport.get_paged(ENDPOINT, BASE_REQUEST, sign_path=SIGN_PATH)

    assert result == [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}, {"n": 5}]
    assert len(transport.calls) == 3
    # The first request carries no cursor; each later one carries the server's
    # cursor unchanged. Everything else in the request is preserved.
    assert transport.calls[0]["data_obj"] == BASE_REQUEST
    assert transport.calls[1]["data_obj"] == {**BASE_REQUEST, "next_key": "cursor-1"}
    assert transport.calls[2]["data_obj"] == {**BASE_REQUEST, "next_key": "cursor-2"}
    for call in transport.calls:
        assert call["endpoint_path"] == ENDPOINT
        assert call["sign_path"] == SIGN_PATH


def test_does_not_mutate_callers_data_obj():
    transport = ScriptedTransport(
        [
            page([{"n": 1}], has_more=True, next_key="cursor-1"),
            page([{"n": 2}], has_more=False),
        ]
    )
    data_obj = dict(BASE_REQUEST)

    transport.get_paged(ENDPOINT, data_obj)

    assert data_obj == BASE_REQUEST
    assert "next_key" not in data_obj


def test_cursor_is_threaded_unchanged():
    # Opaque cursors (here a non-str, structured one) must not be rewritten.
    cursor = {"offset": 7, "token": "opaque=="}
    transport = ScriptedTransport(
        [
            page([{"n": 1}], has_more=True, next_key=cursor),
            page([{"n": 2}], has_more=False),
        ]
    )

    transport.get_paged(ENDPOINT, BASE_REQUEST)

    assert transport.calls[1]["data_obj"]["next_key"] == cursor


def test_custom_list_key():
    transport = ScriptedTransport(
        [
            {"code": 0, "items": [{"n": 1}], "has_more": True, "next_key": "c1"},
            {"code": 0, "items": [{"n": 2}], "has_more": False},
        ]
    )

    assert transport.get_paged(ENDPOINT, BASE_REQUEST, list_key="items") == [
        {"n": 1},
        {"n": 2},
    ]


def test_stops_at_max_pages():
    transport = ScriptedTransport(
        [
            page([{"n": 1}], has_more=True, next_key="cursor-1"),
            page([{"n": 2}], has_more=True, next_key="cursor-2"),
            page([{"n": 3}], has_more=True, next_key="cursor-3"),
        ]
    )

    result = transport.get_paged(ENDPOINT, BASE_REQUEST, max_pages=2)

    assert result == [{"n": 1}, {"n": 2}]
    assert len(transport.calls) == 2


def test_guards_repeated_cursor():
    # The server keeps saying has_more with the same cursor: must stop, not loop
    # forever, and must not raise. (Only three pages are scripted, so a runaway
    # loop would hit the AssertionError in ScriptedTransport.)
    transport = ScriptedTransport(
        [
            page([{"n": 1}], has_more=True, next_key="stuck"),
            page([{"n": 2}], has_more=True, next_key="stuck"),
            page([{"n": 3}], has_more=True, next_key="stuck"),
        ]
    )

    result = transport.get_paged(ENDPOINT, BASE_REQUEST)

    # Page 2 was fetched with cursor "stuck" and answered with "stuck" again, so
    # its items are kept and the loop ends there.
    assert result == [{"n": 1}, {"n": 2}]
    assert len(transport.calls) == 2


def test_guards_cursor_equal_to_callers_initial_cursor():
    transport = ScriptedTransport(
        [page([{"n": 1}], has_more=True, next_key="start")]
    )

    result = transport.get_paged(ENDPOINT, {**BASE_REQUEST, "next_key": "start"})

    assert result == [{"n": 1}]
    assert len(transport.calls) == 1
    assert transport.calls[0]["data_obj"]["next_key"] == "start"


def test_guards_missing_or_empty_cursor_while_has_more():
    for bad_cursor_page in (
        {"code": 0, "data_list": [{"n": 1}], "has_more": True},  # no next_key
        {"code": 0, "data_list": [{"n": 1}], "has_more": True, "next_key": None},
        {"code": 0, "data_list": [{"n": 1}], "has_more": True, "next_key": ""},
    ):
        transport = ScriptedTransport([bad_cursor_page])

        result = transport.get_paged(ENDPOINT, BASE_REQUEST)

        assert result == [{"n": 1}]
        assert len(transport.calls) == 1


def test_empty_first_page_returns_empty_list():
    transport = ScriptedTransport([page([], has_more=False)])

    assert transport.get_paged(ENDPOINT, BASE_REQUEST) == []
    assert len(transport.calls) == 1


def test_missing_list_key_is_treated_as_empty():
    transport = ScriptedTransport([{"code": 0}])

    assert transport.get_paged(ENDPOINT, BASE_REQUEST) == []
    assert len(transport.calls) == 1


def nested_page(items, *, has_more=False, next_key=None):
    """A page shaped like the real decrypted response: the business data sits
    under ``result`` and only ``code`` is top-level."""
    body = {"data_list": items, "has_more": has_more}
    if next_key is not None:
        body["next_key"] = next_key
    return {"code": 0, "message": "ok", "result": body}


def test_unwraps_result_envelope_across_pages():
    transport = ScriptedTransport(
        [
            nested_page([{"n": 1}, {"n": 2}], has_more=True, next_key="cursor-1"),
            nested_page([{"n": 3}], has_more=True, next_key="cursor-2"),
            nested_page([{"n": 4}], has_more=False),
        ]
    )

    result = transport.get_paged(ENDPOINT, BASE_REQUEST, sign_path=SIGN_PATH)

    assert result == [{"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]
    assert len(transport.calls) == 3
    # The cursor comes from result.next_key and is threaded unchanged.
    assert transport.calls[0]["data_obj"] == BASE_REQUEST
    assert transport.calls[1]["data_obj"] == {**BASE_REQUEST, "next_key": "cursor-1"}
    assert transport.calls[2]["data_obj"] == {**BASE_REQUEST, "next_key": "cursor-2"}


def test_unwraps_result_envelope_with_custom_list_key_and_guards():
    transport = ScriptedTransport(
        [
            {
                "code": 0,
                "result": {"items": [{"n": 1}], "has_more": True, "next_key": "k"},
            },
            {
                "code": 0,
                "result": {"items": [{"n": 2}], "has_more": True, "next_key": "k"},
            },
        ]
    )

    result = transport.get_paged(ENDPOINT, BASE_REQUEST, list_key="items")

    # Second page echoes the cursor it was sent with: guard stops, nothing raised.
    assert result == [{"n": 1}, {"n": 2}]
    assert len(transport.calls) == 2


def test_nested_envelope_with_null_result_is_empty_not_a_crash():
    transport = ScriptedTransport([{"code": 0, "result": None}])

    assert transport.get_paged(ENDPOINT, BASE_REQUEST) == []
    assert len(transport.calls) == 1
