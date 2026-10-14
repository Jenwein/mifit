"""Download links for FDS files: the signed ``gen_download_url`` request (spec §3, §4.3).

One request asks for several files of one source (``did`` is the row's ``sid``):
each item is a file's report time and ``suffix``. The service answers a map keyed by
``server_key``; each entry holds a presigned link, valid for about 30 minutes, and
the file's AES key (``obj_key``). A file that does not exist still gets an entry
with a link, but without ``obj_key``: its download would be an HTTP 404, so it is
not worth one.

The request goes to ``/healthapp/service/gen_download_url`` and is signed over
``/service/gen_download_url``: the same rule as the M5 profile endpoints.

A link and a key are bearer secrets: ``FdsEntry`` keeps them in memory only, and
its ``repr`` shows the host, the method and whether there is a key, never the
link or the key. ``obj_name`` is not kept at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from mifit.domains.base import as_int
from mifit.errors import ApiError
from mifit.transport import Transport

from .ids import FileRef, suffix

ENDPOINT = "service/gen_download_url"
SIGN_PATH = "/service/gen_download_url"
BASE_PATH = "healthapp/"

_NO_FILE_MAP = "The download-link answer carried no file map."


@dataclass(frozen=True, repr=False)
class FdsEntry:
    """One file's link, as the service answered it."""

    url: str  # the presigned link: a bearer credential, never output
    key_b64: str | None  # obj_key (base64url); None when the file does not exist
    method: str | None  # the HTTP method the link is signed for ("GET")
    expires_s: int | None  # when the link expires, in Unix seconds

    def __repr__(self) -> str:
        try:
            host = urlsplit(self.url).hostname
        except ValueError:
            host = None
        has_key = self.key_b64 is not None
        return f"FdsEntry(host={host!r}, method={self.method!r}, has_key={has_key})"


def _entry(raw: object) -> FdsEntry | None:
    """The entry for one value of the answer's map, or ``None`` when it has no link."""
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not isinstance(url, str):
        return None
    key = raw.get("obj_key")
    method = raw.get("method")
    return FdsEntry(
        url=url,
        key_b64=key if isinstance(key, str) and key else None,
        method=method if isinstance(method, str) else None,
        expires_s=as_int(raw.get("expires_time")),
    )


def request_urls(t: Transport, sid: str, refs: Sequence[FileRef]) -> dict[str, FdsEntry]:
    """The links for ``refs`` (files of the source ``sid``), keyed by ``server_key``.

    One signed request for all of them. An entry whose value is not an object or
    carries no string ``url`` is left out; an entry whose ``obj_key`` is missing or
    empty gets ``key_b64=None`` (the file does not exist). Raises ``ApiError`` when
    the answer's ``result`` is not a map; ``ApiError``, ``AuthError`` and
    ``OSError`` from the request propagate.
    """
    data = {
        "did": sid,
        "items": [{"timestamp": ref.timestamp, "suffix": suffix(ref)} for ref in refs],
    }
    payload = t.get_json(ENDPOINT, data, sign_path=SIGN_PATH, base_path=BASE_PATH)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ApiError(_NO_FILE_MAP)
    entries: dict[str, FdsEntry] = {}
    for key, raw in result.items():
        entry = _entry(raw)
        if entry is not None:
            entries[key] = entry
    return entries
