"""Provider-neutral step model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StepRecord:
    """Steps for one day.

    ``date`` is the local calendar date (``YYYY-MM-DD``) the steps were taken on and
    ``epoch_s`` is that day's local midnight in Unix seconds. ``distance_m`` (metres)
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
