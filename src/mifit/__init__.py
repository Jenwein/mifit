"""mifit: read your Mi Fitness data from the Xiaomi cloud."""

import importlib.metadata

from mifit.auth.session import Session
from mifit.client import MiFit
from mifit.errors import (
    ApiError,
    AuthError,
    MiFitError,
    NotificationRequiredError,
    RenewalError,
)
from mifit.models.activity import ActivityDailySummary
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary, HeartRatePoint
from mifit.models.profile import UserProfile
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.spo2 import Spo2DailySummary, Spo2Point
from mifit.models.steps import StepRecord
from mifit.models.stress import StressDailySummary
from mifit.models.weight import WeightRecord
from mifit.models.workout_detail import TrackPoint, WorkoutDetail, WorkoutSample
from mifit.models.workouts import Workout


def _installed_version(dist: str = "mifit") -> str:
    """The version of the installed ``dist`` distribution, from its metadata.

    The single source of the version is ``pyproject.toml``; an install (regular
    or editable) records it in the metadata read here. Imported from a source
    tree that was never installed, there is no metadata: ``"0+unknown"``.
    """
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _installed_version()

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
    "WorkoutDetail",
    "WorkoutSample",
    "TrackPoint",
    "Spo2DailySummary",
    "Spo2Point",
    "StressDailySummary",
    "ActivityDailySummary",
    "WeightRecord",
    "UserProfile",
]
