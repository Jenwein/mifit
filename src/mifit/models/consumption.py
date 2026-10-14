"""Provider-neutral daily energy-consumption (calories) model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConsumptionRecord:
    """Energy figures for one day, in kilocalories.

    ``date`` is the ISO calendar date (``YYYY-MM-DD``) of ``epoch_s`` in UTC and
    ``epoch_s`` is the UTC start of that day in Unix seconds. ``resting_kcal`` is the
    resting (basal metabolic) energy, ``active_kcal`` the energy spent on activity
    and ``intake_kcal`` the energy eaten. Each is ``None`` when the source did not
    report it, never zero; a reported ``0`` (e.g. nothing logged as eaten) stays ``0``.
    ``total_kcal`` is ``resting_kcal + active_kcal`` and is ``None`` unless both
    are present.
    """

    date: str
    epoch_s: int
    resting_kcal: float | None
    active_kcal: float | None
    intake_kcal: float | None
    total_kcal: float | None
