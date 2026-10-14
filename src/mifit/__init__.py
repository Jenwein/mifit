"""mifit: read your Mi Fitness data from the Xiaomi cloud."""

from mifit.auth.session import Session
from mifit.client import MiFit
from mifit.errors import (
    ApiError,
    AuthError,
    MiFitError,
    NotificationRequiredError,
    RenewalError,
)
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary, HeartRatePoint
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.spo2 import Spo2DailySummary
from mifit.models.steps import StepRecord
from mifit.models.workouts import Workout

__all__ = [
    "MiFit",
    "Session",
    "MiFitError",
    "AuthError",
    "ApiError",
    "NotificationRequiredError",
    "RenewalError",
    "StepRecord",
    "HeartRatePoint",
    "HeartRateDailySummary",
    "ConsumptionRecord",
    "SleepRecord",
    "SleepSession",
    "SleepStage",
    "Workout",
    "Spo2DailySummary",
]
