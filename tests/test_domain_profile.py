"""Profile domain tests. A fake ``Transport`` serves a synthetic profile; nothing
here touches the network and no real profile data is used.

The answer follows the live-verified shape (notes/11 §3): ``GET
healthapp/user/get_miot_user_profile`` without a ``data`` parameter, signed over
``/user/get_miot_user_profile``, decrypting to ``{code, message, result}`` with the
profile object under ``result``. Every value below (name, birth date, ids,
measurements, goals) is synthetic.
"""

import copy
import dataclasses

import pytest

from mifit.domains.base import REGISTRY
from mifit.domains.profile import fetch_profile
from mifit.models.profile import UserProfile
from mifit.region import BASE_PATH
from mifit.transport import Transport

ABSENT = object()
"""Stands for a key that is not there at all."""


def profile_result(**overrides):
    """The live-verified profile object (synthetic values), with overrides; a field
    set to ``ABSENT`` is left out of the dict."""
    result = {
        "id": "x1",
        "xiaomi_id": 1000001,
        "name": "synthetic",
        "sex": "male",
        "birth": "1990-01-01",
        "age": 35,
        "height": 175,
        "weight": 70,
        "initial_weight": {"weight": 70, "timestamp": 1600000000},
        "daily_step_goal": 8000,
        "daily_cal_goal": 300,
        "daily_sleep_goal": 480,
        "stand_goal": 0,
        "regular_goal_list": [
            {"field": 1, "target": 8000},
            {"field": 2, "target": 300},
            {"field": 4, "target": 30},
        ],
        "record_max_hrm": {"hrm": 190, "source": "auto"},
        "region": "CN",
        "personalization": None,
        "wear_medal": None,
    }
    result.update(overrides)
    return {k: v for k, v in result.items() if v is not ABSENT}


def envelope(result=ABSENT):
    """A decrypted answer; ``result=ABSENT`` leaves the ``result`` key out."""
    payload = {"code": 0, "message": "ok"}
    if result is not ABSENT:
        payload["result"] = result
    return payload


class FakeTransport(Transport):
    """Replaces ``get_json`` with a canned payload and records each call's arguments."""

    def __init__(self, payload):
        # No Session/http needed: ``get_json`` is fully replaced below.
        self._payload = payload
        self.calls = []

    def get_json(self, endpoint_path, data_obj, *, sign_path=None, base_path=BASE_PATH):
        self.calls.append(
            (endpoint_path, data_obj, {"sign_path": sign_path, "base_path": base_path})
        )
        return self._payload


def test_profile_requests_healthapp_without_data():
    transport = FakeTransport(envelope(profile_result()))

    fetch_profile(transport)

    assert transport.calls == [
        (
            "user/get_miot_user_profile",
            None,
            {"sign_path": "/user/get_miot_user_profile", "base_path": "healthapp/"},
        )
    ]


def test_maps_basics():
    result = fetch_profile(FakeTransport(envelope(profile_result())))

    assert result.records == [
        UserProfile(
            height_cm=175.0,
            weight_kg=70.0,
            step_goal=8000,
            calorie_goal_kcal=300,
            activity_goal_min=30,
        )
    ]
    (profile,) = result.records
    # The service sends whole numbers; the measurements are floats all the same.
    assert isinstance(profile.height_cm, float)
    assert isinstance(profile.weight_kg, float)
    assert result.notes == []


def test_raw_keeps_the_profile_untouched():
    sent = profile_result()
    expected = copy.deepcopy(sent)

    result = fetch_profile(FakeTransport(envelope(sent)))

    assert result.raw == [expected]
    (raw,) = result.raw
    assert raw["name"] == "synthetic"
    assert raw["birth"] == "1990-01-01"
    assert raw["regular_goal_list"] == expected["regular_goal_list"]


def test_missing_or_zero_basics_become_none():
    sent = profile_result(
        height=ABSENT, weight=0, daily_step_goal="8000", daily_cal_goal=True
    )

    result = fetch_profile(FakeTransport(envelope(sent)))

    assert result.records == [
        UserProfile(
            height_cm=None,
            weight_kg=None,
            step_goal=None,
            calorie_goal_kcal=None,
            activity_goal_min=30,
        )
    ]
    assert result.raw == [sent]
    assert result.notes == []


@pytest.mark.parametrize("answer", [ABSENT, None, []], ids=["absent", "null", "list"])
def test_answer_without_profile_object_gives_a_note(answer):
    result = fetch_profile(FakeTransport(envelope(answer)))

    assert result.records == []
    assert result.raw == []
    assert result.notes == ["The profile answer carried no profile object."]


def test_unverified_and_personal_fields_stay_out_of_b():
    assert {f.name for f in dataclasses.fields(UserProfile)} == {
        "height_cm",
        "weight_kg",
        "step_goal",
        "calorie_goal_kcal",
        "activity_goal_min",
    }


@pytest.mark.parametrize(
    "goals",
    [
        [{"field": 1, "target": 8000}, {"field": 4, "target": 45}],
        [{"field": 4, "target": 45}, {"field": 2, "target": 300}],
        ["junk", None, {"field": 4, "target": 45}],
        [{"field": 4, "target": 45}, {"field": 4, "target": 60}],
        [{"field": 4.0, "target": 45.9}],
    ],
    ids=["after-others", "before-others", "after-junk", "first-of-two", "floats"],
)
def test_activity_goal_is_the_first_field_4_target(goals):
    sent = profile_result(regular_goal_list=goals)

    (profile,) = fetch_profile(FakeTransport(envelope(sent))).records

    assert profile.activity_goal_min == 45


@pytest.mark.parametrize(
    "goals",
    [
        ABSENT,
        None,
        {"field": 4, "target": 30},
        [],
        [{"field": 1, "target": 8000}, {"field": 2, "target": 300}],
        [{"field": "4", "target": 30}],
        [{"field": True, "target": 30}],
        [{"field": 4}],
        [{"field": 4, "target": 0}],
        [{"field": 4, "target": -30}],
        [{"field": 4, "target": "30"}],
        [{"field": 4, "target": 0}, {"field": 4, "target": 30}],
    ],
    ids=[
        "absent",
        "null",
        "object",
        "empty",
        "no-field-4",
        "string-field",
        "bool-field",
        "no-target",
        "zero-target",
        "negative-target",
        "string-target",
        "first-decides",
    ],
)
def test_activity_goal_is_none_when_absent_or_unusable(goals):
    sent = profile_result(regular_goal_list=goals)

    result = fetch_profile(FakeTransport(envelope(sent)))

    assert result.records == [UserProfile(175.0, 70.0, 8000, 300, None)]
    assert result.notes == []
    assert result.raw == [sent]


def test_step_and_calorie_goals_ignore_the_goal_list():
    goals = [
        {"field": 1, "target": 9000},
        {"field": 2, "target": 400},
        {"field": 4, "target": 30},
    ]

    (profile,) = fetch_profile(
        FakeTransport(envelope(profile_result(regular_goal_list=goals)))
    ).records
    (no_step_goal,) = fetch_profile(
        FakeTransport(
            envelope(profile_result(regular_goal_list=goals, daily_step_goal=ABSENT))
        )
    ).records

    assert profile.step_goal == 8000
    assert profile.calorie_goal_kcal == 300
    # No fallback to the list's field 1.
    assert no_step_goal.step_goal is None


def test_profile_is_not_registered():
    # Not a time series: a facade method and a CLI command, not an export domain.
    assert "profile" not in REGISTRY
