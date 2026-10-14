"""Provider-neutral account-profile model.

Field names describe the measurement or goal, not any service's wire format;
mapping a service's payload onto this record is the job of the domain that
fetches it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserProfile:
    """The account's body measurements and daily goals as set in the app.

    ``height_cm`` is the height in centimetres. ``weight_kg`` is the weight stored
    in the profile, in kilograms (it matched the latest weighing on the one
    account checked). ``step_goal`` is the daily step goal and
    ``calorie_goal_kcal`` the daily calorie goal. Each is ``None`` when not set.

    Personal details (name, birth date, sex, account ids) and the goals whose
    meaning is unverified are deliberately absent: they stay in the domain
    result's ``raw``.
    """

    height_cm: float | None
    weight_kg: float | None
    step_goal: int | None
    calorie_goal_kcal: int | None
