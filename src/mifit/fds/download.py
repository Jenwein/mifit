"""Download one FDS file body from its presigned link (spec §4.4, §11, D8).

A presigned link is a bearer credential for about 30 minutes, and the object store
is not the API host, so the downloader is separate from ``Transport`` and sends
nothing of the session:

* Each download is exactly ``http.get(url, timeout=timeout_s,
  allow_redirects=False, stream=True)``: no header, cookie or credential of its
  own. By default ``http`` is the ``requests`` module, whose ``get`` opens a fresh
  session per call, so no cookie jar carries anything from one download to the next.
* The link is checked before any request: ``https`` only, no user name or password,
  and a host under one of ``host_suffixes`` (``.fds.api.xiaomi.com``). A link with
  whitespace, a control character, a backslash or a non-ASCII character is refused
  too, and so is a host name outside ``[a-z0-9.-]``: URL parsers disagree on such
  links (``requests`` ends the host at a backslash, ``urlsplit`` does not), and the
  host that was checked must be the host connected to.
* A redirect is never followed, and only HTTP 200 counts.
* The body is streamed and abandoned once it exceeds ``max_bytes`` (16 MiB; a live
  body is about 110 KB at most).
* Each download is paced like ``Transport``: ``delay_s`` plus up to half of it at
  random, slept before the request (a refused link sends none and is not paced).

Every failure raises ``FdsUnavailable``, whose message is a fixed fragment a note
can embed: ``"HTTP 404"``, ``"network error (ConnectTimeout)"``, ``"unexpected host
<host>"``. It never quotes the link. A network error's own message does (every
``requests`` error is an ``OSError`` that names the URL), so its replacement is
raised outside the ``except`` block and keeps no ``__context__``.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from mifit.errors import MiFitError

MAX_BODY_BYTES = 16 * 1024 * 1024
HOST_SUFFIXES = (".fds.api.xiaomi.com",)

_CHUNK_BYTES = 65536
_JITTER_FRACTION = 0.5
"""Up to this fraction of ``delay_s`` is added at random to each pause, as in ``Transport``."""
_MIB = 1024 * 1024

# Printable ASCII without space and backslash: the characters every parser reads alike.
_LINK_CHARS = re.compile(r"[!-\[\]-~]+")
_HOST_CHARS = re.compile(r"[a-z0-9.-]+")

_MALFORMED = "malformed link"
_NOT_HTTPS = "not an https link"
_USER_NAME = "link carries a user name"


class FdsUnavailable(MiFitError):
    """A file that could not be downloaded. The message is a fixed fragment (an HTTP
    status, an exception type, a host name), never the link."""


def _size(n: int) -> str:
    return f"{n // _MIB} MiB" if n and n % _MIB == 0 else f"{n} bytes"


class FdsDownloader:
    """Fetches presigned FDS links with ``http`` (the ``requests`` module by default).

    ``http`` is any object with ``.get(url, timeout=..., allow_redirects=...,
    stream=...)`` returning a response with ``.status_code``, ``.iter_content(size)``
    and ``.close()``. It never sees the session: the links carry their own signature.

    ``host_suffixes`` lists the accepted host-name endings, each starting with a dot
    (``TypeError`` for a single string, ``ValueError`` for a suffix without its dot).
    """

    def __init__(
        self,
        http: Any = None,
        *,
        delay_s: float = 1.0,
        timeout_s: float = 30.0,
        max_bytes: int = MAX_BODY_BYTES,
        host_suffixes: Sequence[str] = HOST_SUFFIXES,
    ) -> None:
        # One string would be read as a sequence of one-letter suffixes, and a
        # suffix without its leading dot would let any host that ends alike in.
        if isinstance(host_suffixes, str):
            raise TypeError("host_suffixes must be a sequence of suffixes, not one string.")
        suffixes = tuple(s.lower() for s in host_suffixes)
        if not all(len(s) > 1 and s.startswith(".") for s in suffixes):
            raise ValueError("Each host suffix must start with a dot, like '.fds.api.xiaomi.com'.")
        if http is None:
            import requests  # imported lazily so an injected ``http`` needs no requests

            http = requests
        self._http = http
        self._delay_s = delay_s
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._host_suffixes = suffixes

    def fetch(self, url: str) -> bytes:
        """The body behind ``url``, or ``FdsUnavailable``.

        Refuses a link that is not ``https``, carries a user name or password, or
        names a host outside ``host_suffixes`` (no request, no pause). Otherwise
        pauses, sends one GET, and fails on a redirect, any status but 200, a body
        over ``max_bytes`` or a network error.
        """
        refusal = self._refusal(url)
        if refusal is not None:
            raise FdsUnavailable(refusal)
        if self._delay_s > 0:
            time.sleep(self._delay_s + random.uniform(0, _JITTER_FRACTION * self._delay_s))
        failure: str | None = None
        try:
            return self._get(url)
        except OSError as exc:
            # Raised after the block: the original names the URL.
            failure = f"network error ({type(exc).__name__})"
        raise FdsUnavailable(failure)

    def _refusal(self, url: str) -> str | None:
        """Why ``url`` must not be fetched, or ``None`` when it may be."""
        if _LINK_CHARS.fullmatch(url) is None:
            return _MALFORMED
        try:
            parts = urlsplit(url)
            host = parts.hostname
            has_user = parts.username is not None or parts.password is not None
            _ = parts.port  # raises ValueError for a port that is not a number in 0..65535
        except ValueError:
            return _MALFORMED
        if parts.scheme != "https":
            return _NOT_HTTPS
        if has_user:
            return _USER_NAME
        if host is None or _HOST_CHARS.fullmatch(host) is None:
            return _MALFORMED
        if not host.endswith(self._host_suffixes):
            return f"unexpected host {host}"
        return None

    def _get(self, url: str) -> bytes:
        response = self._http.get(
            url, timeout=self._timeout_s, allow_redirects=False, stream=True
        )
        try:
            status = response.status_code
            if 300 <= status < 400:
                raise FdsUnavailable(f"redirect (HTTP {status}) not followed")
            if status != 200:
                raise FdsUnavailable(f"HTTP {status}")
            chunks = []
            total = 0
            for chunk in response.iter_content(_CHUNK_BYTES):
                total += len(chunk)
                if total > self._max_bytes:
                    raise FdsUnavailable(f"larger than {_size(self._max_bytes)}")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()
