"""Provider-neutral SpO2 (blood-oxygen) model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Spo2DailySummary:
    """One local day's blood-oxygen figures.

    ``date`` is the local date and ``epoch_s`` its local midnight. The three
    figures are blood-oxygen saturation in percent for that day as the service
    reports it (the average is the service's own, not a mean of the day's
    points). Each is ``None`` when the service did not report it.
    """

    date: str
    epoch_s: int
    avg_spo2_pct: int | None
    max_spo2_pct: int | None
    min_spo2_pct: int | None
