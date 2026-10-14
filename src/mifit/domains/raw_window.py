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

from mifit.domains.base import as_int
from mifit.transport import Transport

RAW_ENDPOINT = "data/get_fitness_data_by_time"

START_MARGIN_S = 86400
"""How much earlier than the caller's start the windowed read asks the service."""


def fetch_raw_window(
    t: Transport, key: str, start_epoch_s: int, end_epoch_s: int
) -> tuple[list[dict], int]:
    """Read raw ``key`` rows between two Unix-second timestamps, both inclusive.

    Sends one paged request starting ``START_MARGIN_S`` early, so the service's
    first bucket lies before the window, and returns ``(kept, unusable)``:
    ``kept`` is the entries whose outer ``time`` is within
    ``[start_epoch_s, end_epoch_s]``, in the service's order and untouched (their
    ``value`` is still the wire's JSON string); ``unusable`` counts the entries
    that are not objects or have no usable outer ``time``, so the caller can note
    them. The margin's rows, and any after the end, are dropped without being
    counted.
    """
    entries = t.get_paged(
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
    for entry in entries:
        epoch_s = as_int(entry.get("time")) if isinstance(entry, dict) else None
        if epoch_s is None:
            unusable += 1
        elif start_epoch_s <= epoch_s <= end_epoch_s:
            kept.append(entry)
    return kept, unusable
