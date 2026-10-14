"""Provider-neutral heart-rate model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HeartRatePoint:
    """One heart-rate sample.

    ``epoch_s`` is the sample time in Unix seconds and ``bpm`` is beats per
    minute exactly as reported (no scaling). ``kind`` is the source's own
    sample-type integer, kept uninterpreted, or ``None`` when it was absent.
    """

    epoch_s: int
    bpm: int
    kind: int | None


@dataclass
class HeartRateDailySummary:
    """One local day's heart-rate figures.

    ``date`` is the local date and ``epoch_s`` its local midnight. The four
    figures are beats per minute for that day; ``resting_bpm`` is the day's
    resting heart rate. Each is ``None`` when the source did not report it.
    """

    date: str
    epoch_s: int
    avg_bpm: int | None
    max_bpm: int | None
    min_bpm: int | None
    resting_bpm: int | None
