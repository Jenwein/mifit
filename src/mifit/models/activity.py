"""Provider-neutral daily activity model.

Field names describe the measurement, not any service's wire format; mapping a
service's payload onto this record is the job of the domain that fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActivityDailySummary:
    """One local day's activity figures, as the app's activity page (活力指标)
    shows them, in the page's order.

    ``date`` is the local date and ``epoch_s`` its local midnight.
    ``activity_count`` is the app's activity count (活动次数): a count with no
    unit, which on the one day checked equaled the number of hours of the day with
    a recorded stand interval (0-24). ``activity_min`` is the app's activity time
    (活动时长), in minutes. ``active_kcal`` is the app's calories on that page
    (卡路里): the energy spent on activity, in kcal, the same figure as
    ``ConsumptionRecord.active_kcal``.

    ``0`` is a real value (none that day); each figure is ``None`` when the service
    did not report it.
    """

    date: str
    epoch_s: int
    activity_count: int | None
    activity_min: int | None
    active_kcal: int | None
