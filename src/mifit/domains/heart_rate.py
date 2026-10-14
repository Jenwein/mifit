"""Heart-rate domain: raw heart-rate samples from the fitness-data endpoint.

Each returned row carries its payload as a JSON *string* in ``value``
(``{"time", "bpm", "type"}``), which is decoded a second time here. The decoded
dicts go to ``DomainResult.raw`` untouched; ``records`` holds the normalized
``HeartRatePoint`` view of them. ``bpm`` is the wire value as is: the x5 factor
seen in app logs is a logging artifact and is not applied here.

The service answers a time window bucket by bucket (notes/08). Samples are
grouped into buckets of a few hours (2 h on the account checked), and a bucket
keyed at or before ``start_time`` is left out whole, including its samples after
``start_time``. The end is exact: a sample at ``end_time`` is returned and none
after it. A window starting at a local midnight therefore lost the day's first
bucket. ``fetch_heart_rate_window`` asks for one day more before the start
(``_START_MARGIN_S``, far wider than a bucket) and keeps only the rows whose
outer ``time`` lies in the requested window, the same pattern the sleep domain
uses.

``fetch_heart_rate_full`` remains for paging back without a start: it omits
``start_time`` and follows the service's cursor back from ``end_time``, as the
app's own full pull does.
"""

from __future__ import annotations

import json

from mifit.domains.base import DomainResult, as_int, register
from mifit.models.heart_rate import HeartRatePoint
from mifit.transport import Transport

_ENDPOINT = "data/get_fitness_data_by_time"

_START_MARGIN_S = 86400
"""How much earlier than the caller's start the windowed read asks the service."""


@register("heart_rate")
def fetch_heart_rate_window(
    t: Transport, start_epoch_s: int, end_epoch_s: int
) -> DomainResult:
    """Fetch raw heart-rate samples between two Unix-second timestamps, both
    inclusive.

    The request starts ``_START_MARGIN_S`` early, so the service's first bucket
    lies before the window, and only rows whose outer ``time`` is within
    ``[start_epoch_s, end_epoch_s]`` are kept; the margin's rows are dropped
    without a note. A row without a usable outer ``time`` is skipped with a note
    and left out of ``raw``. A window with no rows gives an empty result, never a
    fabricated sample.
    """
    entries = t.get_paged(
        _ENDPOINT,
        {
            "key": "heart_rate",
            "start_time": start_epoch_s - _START_MARGIN_S,
            "end_time": end_epoch_s,
            "reverse": True,
        },
        list_key="data_list",
    )
    kept = []
    notes = []
    for entry in entries:
        epoch_s = as_int(entry.get("time")) if isinstance(entry, dict) else None
        if epoch_s is None:
            notes.append("Skipped a heart-rate row without a usable time.")
        elif start_epoch_s <= epoch_s <= end_epoch_s:
            kept.append(entry)
    result = _build_result(kept)
    result.notes.extend(notes)
    return result


def fetch_heart_rate_full(
    t: Transport, end_epoch_s: int, max_pages: int = 10
) -> DomainResult:
    """Fetch raw heart-rate samples backwards from ``end_epoch_s``, paginated.

    Sends no ``start_time``, which is the candidate fuller read, and follows
    the service's cursor for at most ``max_pages`` pages. Not registered in the
    domain registry: its signature differs from the registry's
    ``(transport, start_epoch_s, end_epoch_s)`` contract.

    When the page cap stops the read while the service still had older points,
    the result carries a note saying so; a read that ends on its own does not.
    """
    paged = t.get_paged_result(
        _ENDPOINT,
        {
            "key": "heart_rate",
            "end_time": end_epoch_s,
            "reverse": True,
        },
        list_key="data_list",
        max_pages=max_pages,
    )
    result = _build_result(paged.items)
    if paged.truncated:
        result.notes.append(
            f"Full heart-rate read stopped at its {max_pages}-page cap while the "
            "service still had older points: points before the earliest one "
            "returned are missing."
        )
    return result


def _build_result(entries: list[dict]) -> DomainResult:
    """Decode entries into raw values and records, skipping unusable rows with a note."""
    result = DomainResult()
    for entry in entries:
        try:
            value = json.loads(entry["value"])
        except (KeyError, TypeError, ValueError):
            result.notes.append(
                "Skipped a heart-rate row whose value is missing or not valid JSON."
            )
            continue
        if not isinstance(value, dict):
            result.notes.append("Skipped a heart-rate row whose value is not an object.")
            continue

        result.raw.append(value)
        point = _to_point(value)
        if point is None:
            result.notes.append(
                "Skipped a heart-rate row without a usable time or bpm."
            )
            continue
        result.records.append(point)
    return result


def _to_point(value: dict) -> HeartRatePoint | None:
    try:
        epoch_s = int(value["time"])
        bpm = int(value["bpm"])
        kind = None if value.get("type") is None else int(value["type"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return HeartRatePoint(epoch_s=epoch_s, bpm=bpm, kind=kind)
