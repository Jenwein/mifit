"""SpO2 domain: raw all-day blood-oxygen samples from the fitness-data endpoint.

Each returned row is an outer object whose ``time`` is the sample's instant and
whose ``value`` is a JSON *string* (``{"time", "spo2"}``) that is decoded a
second time here. The decoded dicts go to ``DomainResult.raw`` untouched;
``records`` holds the normalized ``Spo2Point`` view of them, timed by the outer
``time`` (equal to ``value.time`` on every row seen, notes/11 §1.2).

Raw ``spo2`` loses the bucket keyed at or before ``start_time`` exactly as raw
heart rate does (notes/11 §1.3), so the read goes through
``raw_window.fetch_raw_window``: it asks a day earlier and keeps only the rows in
the requested window.

Spot checks (``single_spo2``) and low-SpO2 events (``low_spo2``) are separate
keys and are not read here; ``MiFit.raw`` returns them as the service sends them.
"""

from __future__ import annotations

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    as_int,
    as_positive_int,
    decode_value,
    register,
    stalled_note,
)
from mifit.domains.raw_window import fetch_raw_window
from mifit.models.spo2 import Spo2Point
from mifit.transport import Transport

_VALUE_NOTES = {
    ValueProblem.UNREADABLE: (
        "Skipped an SpO2 row whose value is missing or not valid JSON."
    ),
    ValueProblem.NOT_AN_OBJECT: "Skipped an SpO2 row whose value is not an object.",
}


@register("spo2")
def fetch_spo2_window(
    t: Transport, start_epoch_s: int, end_epoch_s: int
) -> DomainResult:
    """Fetch raw SpO2 samples between two Unix-second timestamps, both inclusive.

    The request starts a day early and only rows whose outer ``time`` is within
    ``[start_epoch_s, end_epoch_s]`` are kept; the margin's rows are dropped
    without a note. A row without a usable outer ``time``, or whose value is not
    a JSON object, is skipped with a note and left out of ``raw``. A row whose
    ``spo2`` is absent, ``0``, negative or not a number gives no point (a note
    says so) but its decoded value stays in ``raw``. A window with no rows gives
    an empty result, never a fabricated sample. A read that stalled on an
    unusable cursor ends with a note saying rows may be missing.
    """
    kept, unusable, stalled = fetch_raw_window(t, "spo2", start_epoch_s, end_epoch_s)
    result = DomainResult()
    for entry in kept:
        value, problem = decode_value(entry)
        if problem is not None:
            result.notes.append(_VALUE_NOTES[problem])
            continue

        result.raw.append(value)
        spo2_pct = as_positive_int(value.get("spo2"))
        if spo2_pct is None:
            result.notes.append("Skipped an SpO2 row without a usable SpO2 value.")
            continue
        # ``fetch_raw_window`` kept only rows whose outer ``time`` is usable.
        epoch_s = as_int(entry["time"])
        result.records.append(Spo2Point(epoch_s=epoch_s, spo2_pct=spo2_pct))
    result.notes.extend(["Skipped an SpO2 row without a usable time."] * unusable)
    if stalled:
        result.notes.append(stalled_note("SpO2 rows"))
    return result
