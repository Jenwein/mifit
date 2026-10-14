"""Provider-neutral body-weight model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WeightRecord:
    """One weighing.

    ``epoch_s`` is the time it was recorded (Unix seconds), ``weight_kg`` the body
    weight in kilograms and ``bmi`` the body-mass index stored with it (``None``
    when not reported). Body composition is deliberately absent: it is unverified
    and stays in the domain result's ``raw``.
    """

    epoch_s: int
    weight_kg: float
    bmi: float | None
