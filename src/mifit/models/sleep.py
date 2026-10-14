"""Provider-neutral sleep models.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto these records is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SleepStage:
    """One stretch of a sleep session spent in a single stage.

    ``start_epoch_s``/``end_epoch_s`` are true Unix seconds. ``stage`` is one of
    ``"deep"``, ``"light"``, ``"rem"``, ``"awake"`` or ``"unknown"`` (a stage the
    source reported but that has no verified meaning).
    """

    start_epoch_s: int
    end_epoch_s: int
    stage: str


@dataclass
class SleepSession:
    """One continuous sleep (a night or a nap), from falling asleep to waking up.

    ``start_epoch_s``/``end_epoch_s`` are true Unix seconds; ``utc_offset_s`` is the
    UTC offset of the zone it was recorded in, for showing local clock times
    (``None`` when unknown). ``asleep_min`` excludes time awake. The stage minutes are
    ``None`` when the source did not report them or did not stage this session (a
    nap), never a made-up zero. ``stages`` is the stage timeline, empty when the
    source has none for this session.
    """

    start_epoch_s: int
    end_epoch_s: int
    utc_offset_s: int | None
    asleep_min: int
    deep_min: int | None
    light_min: int | None
    rem_min: int | None
    awake_min: int | None
    stages: list[SleepStage] = field(default_factory=list)


@dataclass
class SleepRecord:
    """Sleep for one day: every session that ended on it.

    ``date`` is the local calendar date (``YYYY-MM-DD``) and ``epoch_s`` that day's
    local midnight in Unix seconds. ``asleep_min`` is the day's total time asleep
    across its sessions. The stage minutes cover only the staged sessions, so with a
    nap on the day they add up to less than ``asleep_min``; they are all ``None`` when
    no session was staged. Any figure the source did not report, or reported as a
    meaningless ``0`` (heart rate, SpO2), is ``None``, never zero.
    """

    date: str
    epoch_s: int
    asleep_min: int
    deep_min: int | None
    light_min: int | None
    rem_min: int | None
    awake_min: int | None
    avg_hr_bpm: int | None
    min_hr_bpm: int | None
    max_hr_bpm: int | None
    avg_spo2_pct: int | None
    min_spo2_pct: int | None
    max_spo2_pct: int | None
    sessions: list[SleepSession] = field(default_factory=list)
