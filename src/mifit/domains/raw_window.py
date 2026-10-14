"""The shared windowed read of raw fitness data (``get_fitness_data_by_time``).

The service answers a time window bucket by bucket (notes/08 §2.3): samples are
grouped into buckets of a few hours (2 h on the account checked), and a bucket
keyed at or before ``start_time`` is left out whole, including its samples after
``start_time``. The end is exact: a sample at ``end_time`` is returned and none
after it. Raw ``heart_rate`` and raw ``spo2`` both lose their first bucket this
way (notes/11 §1.3), so a window starting at a local midnight misses the day's
first hours.

``fetch_raw_window`` asks for one day more before the start (``START_MARGIN_S``,
far wider than a bucket) and keeps only the rows whose outer ``time`` lies in the
requested window. Every domain that reads a bucketed raw key, or one that may be
bucketed, must read it through here. ``MiFit.raw`` deliberately does not: it
returns the service's own answer, unfiltered.
"""

from __future__ import annotations

from typing import NamedTuple

from mifit.domains.base import as_int
from mifit.transport import Transport

RAW_ENDPOINT = "data/get_fitness_data_by_time"

START_MARGIN_S = 86400
"""How much earlier than the caller's start the windowed read asks the service."""


class RawWindow(NamedTuple):
    """What ``fetch_raw_window`` read."""

    kept: list[dict]
    """The entries whose outer ``time`` is in the window, in the service's order
    and untouched (their ``value`` is still the wire's JSON string)."""
    unusable: int
    """How many entries are not objects or have no usable outer ``time``."""
    stalled: bool
    """Whether the paged read stalled on an unusable cursor
    (``PagedResult.stalled``), so rows in the window may be missing."""


def fetch_raw_window(
    t: Transport, key: str, start_epoch_s: int, end_epoch_s: int
) -> RawWindow:
    """Read raw ``key`` rows between two Unix-second timestamps, both inclusive.

    Sends one paged request starting ``START_MARGIN_S`` early, so the service's
    first bucket lies before the window, and returns
    ``RawWindow(kept, unusable, stalled)``: ``kept`` is the entries whose outer
    ``time`` is within ``[start_epoch_s, end_epoch_s]``, in the service's order
    and untouched (their ``value`` is still the wire's JSON string); ``unusable``
    counts the entries that are not objects or have no usable outer ``time``, and
    ``stalled`` says whether the read stalled, so the caller can note both. The
    margin's rows, and any after the end, are dropped without being counted.
    """
    paged = t.get_paged_result(
        RAW_ENDPOINT,
        {
            "key": key,
            "start_time": start_epoch_s - START_MARGIN_S,
            "end_time": end_epoch_s,
            "reverse": True,
        },
        list_key="data_list",
    )
    kept = []
    unusable = 0
    for entry in paged.items:
        epoch_s = as_int(entry.get("time")) if isinstance(entry, dict) else None
        if epoch_s is None:
            unusable += 1
        elif start_epoch_s <= epoch_s <= end_epoch_s:
            kept.append(entry)
    return RawWindow(kept, unusable, paged.stalled)
