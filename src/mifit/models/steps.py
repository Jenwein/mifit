"""Provider-neutral step model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StepRecord:
    """Steps for one day.

    ``date`` is the ISO calendar date (``YYYY-MM-DD``) of ``epoch_s`` in UTC and
    ``epoch_s`` is the record's own timestamp in Unix seconds. ``distance_m`` (metres)
    and ``step_calories`` are ``None`` when the source did not report them, never zero.

    ``step_calories`` is the step report's own walking-calorie figure. It is NOT the
    calorie ring the app shows (resting + active energy); that comes from the
    consumption domain (``ConsumptionRecord``).
    """

    date: str
    steps: int
    distance_m: int | None
    step_calories: int | None
    epoch_s: int
