"""Heart-rate domain: raw heart-rate samples from the fitness-data endpoint.

Each returned row carries its payload as a JSON *string* in ``value``
(``{"time", "bpm", "type"}``), which is decoded a second time here. The decoded
dicts go to ``DomainResult.raw`` untouched; ``records`` holds the normalized
``HeartRatePoint`` view of them. ``bpm`` is the wire value as is: the x5 factor
seen in app logs is a logging artifact and is not applied here.

Two reads are offered. ``fetch_heart_rate_window`` is the plain windowed query,
which can silently drop points, so every result carries a note saying so.
``fetch_heart_rate_full`` omits ``start_time`` and pages back from ``end_time``;
it is the candidate fuller read.
"""

from __future__ import annotations

import json

from mifit.domains.base import DomainResult, register
from mifit.models.heart_rate import HeartRatePoint
from mifit.transport import Transport

_ENDPOINT = "data/get_fitness_data_by_time"

_WINDOW_INCOMPLETE_NOTE = (
    "Windowed heart-rate query: this result MAY BE INCOMPLETE. The service can "
    "drop points from a start/end window and the cause is unknown; compare with "
    "fetch_heart_rate_full before treating it as the full record."
)


@register("heart_rate")
def fetch_heart_rate_window(
    t: Transport, start_epoch_s: int, end_epoch_s: int
) -> DomainResult:
    """Fetch raw heart-rate samples between two Unix-second timestamps.

    The result always carries a note that a windowed query may be incomplete,
    including when it is empty. A window with no rows gives an empty result,
    never a fabricated sample.
    """
    entries = t.get_paged(
        _ENDPOINT,
        {
            "key": "heart_rate",
            "start_time": start_epoch_s,
            "end_time": end_epoch_s,
            "reverse": True,
        },
        list_key="data_list",
    )
    result = _build_result(entries)
    result.notes.insert(0, _WINDOW_INCOMPLETE_NOTE)
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
