"""Weight domain: body-weight records from the fitness-data endpoint.

The key is ``"weight"`` (APK ``FitnessPersistKey.Weight``). Each returned row is an
outer object whose ``time`` is the record's instant and whose ``value`` is a JSON
*string* that is decoded a second time here. The decoded dicts go to
``DomainResult.raw`` untouched; ``records`` holds the normalized ``WeightRecord``
view of them, timed by the outer ``time`` (equal to ``value.time`` on the row
seen, notes/11 §2).

The window goes through ``raw_window.fetch_raw_window``: whether ``weight`` is
bucketed like ``heart_rate`` and ``spo2`` could not be tested (one row on the
account checked), so it is asked a day earlier and filtered like them.

The value carries about 60 fields. Only the weight and the BMI are modelled;
every body-composition figure (fat, muscle, moisture, protein, ...) was ``0`` on
the account checked, so none could be verified (notes/11 §2) and they stay in
``raw`` only.
"""

from __future__ import annotations

from mifit.domains.base import (
    DomainResult,
    ValueProblem,
    as_int,
    as_positive_float,
    decode_value,
    register,
    stalled_note,
)
from mifit.domains.raw_window import fetch_raw_window
from mifit.models.weight import WeightRecord
from mifit.transport import Transport

_VALUE_NOTES = {
    ValueProblem.UNREADABLE: (
        "Skipped a weight row whose value is missing or not valid JSON."
    ),
    ValueProblem.NOT_AN_OBJECT: "Skipped a weight row whose value is not an object.",
}


@register("weight")
def fetch_weight(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult:
    """Fetch weight records between two Unix-second timestamps, both inclusive.

    The request starts a day early and only rows whose outer ``time`` is within
    ``[start_epoch_s, end_epoch_s]`` are kept; the margin's rows are dropped
    without a note. A row without a usable outer ``time``, or whose value is not
    a JSON object, is skipped with a note and left out of ``raw``. A row whose
    ``weight`` is absent, ``0``, negative or not a number gives no record (a note
    says so) but its decoded value stays in ``raw``. The BMI is ``None`` unless it
    is a positive number. A range with no rows gives an empty result, never a
    fabricated record. A read that stalled on an unusable cursor ends with a note
    saying records may be missing.
    """
    kept, unusable, stalled = fetch_raw_window(t, "weight", start_epoch_s, end_epoch_s)
    result = DomainResult()
    for entry in kept:
        value, problem = decode_value(entry)
        if problem is not None:
            result.notes.append(_VALUE_NOTES[problem])
            continue

        result.raw.append(value)
        weight_kg = as_positive_float(value.get("weight"))
        if weight_kg is None:
            result.notes.append("Skipped a weight row without a usable weight.")
            continue
        bmi = as_positive_float(value.get("bmi"))
        # ``fetch_raw_window`` kept only rows whose outer ``time`` is usable.
        epoch_s = as_int(entry["time"])
        result.records.append(
            WeightRecord(epoch_s=epoch_s, weight_kg=weight_kg, bmi=bmi)
        )
    result.notes.extend(["Skipped a weight row without a usable time."] * unusable)
    if stalled:
        result.notes.append(stalled_note("weight rows"))
    return result
