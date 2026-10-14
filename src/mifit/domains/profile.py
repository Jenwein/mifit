"""Profile: the account's body measurements and daily goals.

The profile is read with ``GET healthapp/user/get_miot_user_profile`` (APK
``AccountService.getUserProfile()``): under the ``healthapp/`` base path instead
of ``app/v1/``, with no ``data`` parameter, and signed over
``/user/get_miot_user_profile``, that is the URL path with the ``healthapp/``
prefix stripped and the leading ``/`` kept (notes/11 §3). The answer is encrypted
like any other and carries the profile object under ``result``.

The same service also has ``user/set_miot_user_profile`` and
``user/remove_miot_user_profile``; both write, and this SDK never calls them.

``DomainResult.raw`` is ``[result]``, untouched. It holds personal identifiers
(name, birth date, sex, age, account ids, region), so treat it as personal data.
``records`` holds one ``UserProfile`` built from ``height``, ``weight``,
``daily_step_goal`` and ``daily_cal_goal``; every other field
(``daily_sleep_goal``, ``stand_goal``, ``regular_goal_list``, ``initial_weight``,
``record_max_hrm`` and the personal ones) is unverified or personal and stays in
``raw`` only.

The profile is not a time series, so it is not registered as a domain: it is
read through ``MiFit.profile()`` and ``mifit profile``.
"""

from __future__ import annotations

from mifit.domains.base import DomainResult, as_float, as_positive_int
from mifit.models.profile import UserProfile
from mifit.transport import Transport

_ENDPOINT = "user/get_miot_user_profile"
_SIGN_PATH = "/user/get_miot_user_profile"
_BASE_PATH = "healthapp/"


def _positive(value: float | None) -> float | None:
    """``value`` when it is above zero, else ``None`` (``0`` means "not set")."""
    return value if value is not None and value > 0 else None


def fetch_profile(t: Transport) -> DomainResult:
    """Fetch the account profile: one ``UserProfile`` and the profile object in ``raw``.

    A height or weight that is absent, ``0``, negative or not a number is
    ``None``, and so is a goal that is not a positive number; none of these adds
    a note. An answer without a profile object gives no record, an empty ``raw``
    and a note.
    """
    payload = t.get_json(_ENDPOINT, None, sign_path=_SIGN_PATH, base_path=_BASE_PATH)
    result = payload.get("result")
    if not isinstance(result, dict):
        return DomainResult(notes=["The profile answer carried no profile object."])

    profile = UserProfile(
        height_cm=_positive(as_float(result.get("height"))),
        weight_kg=_positive(as_float(result.get("weight"))),
        step_goal=as_positive_int(result.get("daily_step_goal")),
        calorie_goal_kcal=as_positive_int(result.get("daily_cal_goal")),
    )
    return DomainResult(records=[profile], raw=[result])
