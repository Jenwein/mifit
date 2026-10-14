"""Helpers shared by several test modules (imported as ``from support import ...``;
pytest puts ``tests/`` on ``sys.path``). Every credential and payload here is
synthetic."""

import base64
import copy
import json
from types import SimpleNamespace

from mifit.auth.session import Session
from mifit.crypto import rc4_crypt, signed_nonce
from mifit.transport import PagedResult, Transport

SYNTH_SSECURITY = base64.b64encode(b"synthetic-support-secret").decode("ascii")


def exception_chain(exc):
    """``exc`` and every exception reachable from it through ``__cause__``/``__context__``."""
    seen = []
    pending = [exc]
    while pending:
        link = pending.pop()
        if link is None or any(link is s for s in seen):
            continue
        seen.append(link)
        pending += [link.__cause__, link.__context__]
    return seen


def synthetic_session() -> Session:
    """A session whose every field is synthetic, signed with ``SYNTH_SSECURITY``."""
    return Session(
        user_id="1000001",
        c_user_id="synthetic-support-cuser",
        service_token="SYNTH-SUPPORT-SERVICE-TOKEN-0000",
        ssecurity=SYNTH_SSECURITY,
        region="cn",
        created_at="2026-01-01T00:00:00Z",
    )


class PagedFake(Transport):
    """A ``Transport`` that serves canned rows instead of paging the service.

    ``payload`` is a decrypted envelope (``{"code": 0, "result": {list_key: [...]}}``)
    served for every endpoint; ``by_endpoint`` maps an endpoint path to its own
    envelope (an endpoint it does not name gets ``payload``). ``errors`` maps an
    endpoint path to the exception its paged read raises. Every paged read reports
    ``truncated`` and ``stalled`` as given; ``stalled_endpoints``, when given,
    narrows ``stalled`` to those endpoint paths.

    Each paged read, through ``get_paged`` or ``get_paged_result``, is recorded in
    ``calls``; each ``get_json`` in ``json_calls``. Needs no session and no HTTP
    client: nothing here reaches the network code.
    """

    def __init__(
        self,
        payload=None,
        *,
        by_endpoint=None,
        errors=None,
        truncated=False,
        stalled=False,
        stalled_endpoints=None,
    ):
        self._payload = payload
        self._by_endpoint = by_endpoint or {}
        self._errors = errors or {}
        self._truncated = truncated
        self._stalled = stalled
        self._stalled_endpoints = stalled_endpoints
        self.calls = []
        self.json_calls = []

    def _payload_for(self, endpoint_path):
        return self._by_endpoint.get(endpoint_path, self._payload) or {}

    def get_paged_result(
        self,
        endpoint_path,
        data_obj,
        *,
        list_key="data_list",
        sign_path=None,
        max_pages=None,
    ):
        # The inherited ``get_paged`` calls this, so both entry points are recorded.
        self.calls.append(
            {
                "endpoint_path": endpoint_path,
                "data_obj": copy.deepcopy(data_obj),
                "list_key": list_key,
                "max_pages": max_pages,
            }
        )
        error = self._errors.get(endpoint_path)
        if error is not None:
            raise error
        body = self._payload_for(endpoint_path).get("result") or {}
        stalled = self._stalled and (
            self._stalled_endpoints is None or endpoint_path in self._stalled_endpoints
        )
        return PagedResult(list(body.get(list_key) or []), self._truncated, stalled)

    def get_json(self, endpoint_path, data_obj=None, **kwargs):
        self.json_calls.append(
            {"endpoint_path": endpoint_path, "data_obj": copy.deepcopy(data_obj)}
        )
        return self._payload_for(endpoint_path)


class WindowFake(Transport):
    """A ``Transport`` that models the service's window rule (notes/06, notes/10)
    over ``rows`` instead of paging the service.

    A paged read serves the rows with ``start_time < row["time"] <= end_time``
    (no lower bound when the request has no ``start_time``), newest first, in one
    go: ``truncated`` and ``stalled`` are false. ``by_endpoint`` maps an endpoint
    path to its own rows; any other endpoint gets ``rows``, whatever its
    ``list_key``.

    ``get_json`` models the one-day read (``diet/user_consumption``): it answers
    ``{"code": 0, "result": row["result"]}`` for the row whose ``time`` is the
    request's ``time``, and an empty ``result`` when there is none.

    Each paged read is recorded in ``calls`` and each ``get_json`` in
    ``json_calls``, as ``PagedFake`` records them. Needs no session and no HTTP
    client.
    """

    def __init__(self, rows, *, by_endpoint=None):
        self._rows = list(rows)
        self._by_endpoint = by_endpoint or {}
        self.calls = []
        self.json_calls = []

    def _rows_for(self, endpoint_path):
        return self._by_endpoint.get(endpoint_path, self._rows)

    def get_paged_result(
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
                "data_obj": copy.deepcopy(data_obj),
                "list_key": list_key,
                "max_pages": max_pages,
            }
        )
        start = data_obj.get("start_time")
        end = data_obj["end_time"]
        served = [
            row
            for row in self._rows_for(endpoint_path)
            if (start is None or start < row["time"]) and row["time"] <= end
        ]
        served.sort(key=lambda row: row["time"], reverse=True)
        return PagedResult(served, False, False)

    def get_json(self, endpoint_path, data_obj=None, **kwargs):
        self.json_calls.append(
            {"endpoint_path": endpoint_path, "data_obj": copy.deepcopy(data_obj)}
        )
        wanted = (data_obj or {}).get("time")
        for row in self._rows_for(endpoint_path):
            if row["time"] == wanted:
                return {"code": 0, "result": row["result"]}
        return {"code": 0, "result": {}}


def _encrypt_body(payload, nonce, ssecurity):
    """A response body as the service builds it: b64(RC4(json)) under ``nonce``."""
    key = base64.b64decode(signed_nonce(ssecurity, nonce))
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(rc4_crypt(key, raw)).decode("ascii")


class EncryptingHttp:
    """An ``http`` stand-in for a real ``Transport``: each GET answers status 200
    with the next of ``payloads`` (the last one repeats), encrypted for that
    request's own ``_nonce`` and ``ssecurity``.

    Each GET appends ``("get", None)`` to ``events`` (when given), so a test can
    interleave it with other events; ``calls`` counts the GETs.
    """

    def __init__(self, payloads, *, events=None, ssecurity=SYNTH_SSECURITY):
        self._payloads = list(payloads)
        self._events = events
        self._ssecurity = ssecurity
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        if self._events is not None:
            self._events.append(("get", None))
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        body = _encrypt_body(payload, params["_nonce"], self._ssecurity)
        return SimpleNamespace(status_code=200, text=body)
