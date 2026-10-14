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
