"""Provider-neutral stress model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StressDailySummary:
    """One local day's stress figures.

    ``date`` is the local date and ``epoch_s`` its local midnight. The three
    figures are the day's stress level (a 1-100 index) as the service's daily
    report states them; the average is the service's own. Each is ``None`` when the
    service did not report it.
    """

    date: str
    epoch_s: int
    avg_stress_level: int | None
    max_stress_level: int | None
    min_stress_level: int | None
