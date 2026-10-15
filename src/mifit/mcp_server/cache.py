"""The MCP server's result cache: in memory, for a few minutes.

AI clients often read the same range several times in one conversation; a cached
result answers without another request to Xiaomi. Entries live ``ttl_s`` seconds
(5 minutes by default, so newly synced data does not wait long) and at most
``max_entries`` are kept, the least recently used going first. Nothing is written to
disk: that would be one more copy of the user's health data.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

__all__ = ["TtlCache"]


class TtlCache:
    """Encoded tool results by key. Not thread-safe: the server uses it only from its
    event loop."""

    def __init__(
        self,
        *,
        ttl_s: float = 300.0,
        max_entries: int = 64,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, str]] = OrderedDict()

    def get(self, key: Hashable) -> str | None:
        """The value stored under ``key`` less than ``ttl_s`` seconds ago, or ``None``."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self._clock() - stored_at >= self._ttl_s:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: Hashable, value: str) -> None:
        """Store ``value`` under ``key`` as of now, evicting the least recently used
        entries beyond ``max_entries``."""
        self._entries[key] = (self._clock(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
