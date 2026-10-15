"""CLI tests. ``MiFit``, ``login_qr`` and ``save_session`` are patched on ``mifit.cli``
so nothing here touches the network, the real session file or any real health data.
The session store and the session log (``record_event``) live under the isolated
home (conftest), never the real ``~/.mifit``. Every credential below is synthetic.
"""

import dataclasses
import io
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from mifit import cli
from mifit.auth.session import Session, save_session
from mifit.chunks import plan_chunks
from mifit.cli.partial import append_part, start_partial
from mifit.paths import default_session_log_path
from mifit.domains.base import REGISTRY, DomainResult
from mifit.errors import ApiError, AuthError, NotificationRequiredError
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
from support import exception_chain

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SYNTH_TOKEN = "SYNTH-SERVICE-TOKEN-CLI-0001"
SYNTH_SSECURITY = "c3ludGgtc3NlY3VyaXR5LWNsaQ=="
SYNTH_COOKIE = "SYNTH-EXTRA-COOKIE-CLI-0001"
LOGIN_URL = "https://account.xiaomi.com/synthetic/cli-confirm?t=synthetic-login"
QR_IMAGE_URL = "https://account.xiaomi.com/synthetic/cli-qr-image?t=synthetic-qr"
NOTIFICATION_URL = "https://account.xiaomi.com/synthetic/cli-notify?t=synthetic-notify"

STEP = StepRecord(
    date="2026-10-01", steps=4321, distance_m=3100, step_calories=150, epoch_s=1790812800
)
SLEEP = SleepRecord(
    date="2026-10-01",
    epoch_s=1790812800,
    asleep_min=60,
    deep_min=60,
    light_min=0,
    rem_min=None,
    awake_min=0,
    avg_hr_bpm=60,
    min_hr_bpm=55,
    max_hr_bpm=70,
    avg_spo2_pct=None,
    min_spo2_pct=None,
    max_spo2_pct=None,
    sessions=[
        SleepSession(
            start_epoch_s=1790812800,
            end_epoch_s=1790816400,
            utc_offset_s=28800,
            asleep_min=60,
            deep_min=60,
            light_min=0,
            rem_min=None,
            awake_min=0,
            stages=[SleepStage(1790812800, 1790816400, "deep")],
        )
    ],
)
CONSUMPTION = ConsumptionRecord(
    date="2026-10-01",
    epoch_s=1790812800,
    resting_kcal=1907.5,
    active_kcal=365.0,
    intake_kcal=0.0,
    total_kcal=2272.5,
)
HEART_RATE_POINT = HeartRatePoint(epoch_s=1790812860, bpm=71, kind=None)
HR_DAY = HeartRateDailySummary(
    date="2026-10-01",
    epoch_s=1790812800,
    avg_bpm=70,
    max_bpm=130,
    min_bpm=50,
    resting_bpm=None,
)
SPO2_DAY = Spo2DailySummary(
    date="2026-10-01",
    epoch_s=1790812800,
    avg_spo2_pct=96,
    max_spo2_pct=99,
    min_spo2_pct=None,
)
SPO2_POINT = Spo2Point(epoch_s=1790812860, spo2_pct=97)
STRESS_DAY = StressDailySummary(
    date="2026-10-01",
    epoch_s=1790812800,
    avg_stress_level=35,
    max_stress_level=80,
    min_stress_level=None,
    relaxed_min=600,
    mild_min=300,
    moderate_min=0,
    severe_min=None,
)
WEIGHT = WeightRecord(epoch_s=1790812800, weight_kg=70.5, bmi=None)
PROFILE = UserProfile(height_cm=175.0, weight_kg=70.0, step_goal=8000, calorie_goal_kcal=None)
WORKOUT = Workout(
    start_epoch_s=1790820000,
    end_epoch_s=1790822701,
    utc_offset_s=28800,
    sport="running",
    source_sport_code=1,
    duration_s=2400,
    distance_m=5000,
    active_kcal=350,
    total_kcal=410,
    avg_hr_bpm=148,
    max_hr_bpm=170,
    steps=6000,
)
DETAIL = WorkoutDetail(
    workout=WORKOUT,
    samples=[WorkoutSample(1790820000, 120, 2.5, 3, 170, 360, None)],
    track=[TrackPoint(1790820000, 10.0, 20.0)],
)


# A row as the generic reads return it: the service's outer fields, ``value`` decoded.
ROW_DECODED = {
    "sid": "synthetic-sid",
    "key": "stress",
    "time": 1790812800,
    "tag": "days",
    "value": {"avg_stress": 40},
}


def make_session():
    return Session(
        user_id="1000001",
        c_user_id="synthetic-cuser-id",
        service_token=SYNTH_TOKEN,
        ssecurity=SYNTH_SSECURITY,
        region="cn",
        created_at="2026-10-14T00:00:00Z",
        extra_cookies={"synthetic": SYNTH_COOKIE},
    )


class FakeMiFit:
    """Stands in for ``MiFit``: records the calls and returns canned results."""

    instances = []
    error_on_init = None

    def __init__(self, *args, **kwargs):
        if FakeMiFit.error_on_init is not None:
            raise FakeMiFit.error_on_init
        self.calls = []
        self.session = make_session()
        FakeMiFit.instances.append(self)

    def steps(self, start, end):
        self.calls.append(("steps", start, end, {}))
        return DomainResult(
            records=[STEP], raw=[{"time": 1790812800, "steps": 4321}], notes=[]
        )

    def consumption(self, start, end):
        self.calls.append(("consumption", start, end, {}))
        return DomainResult(
            records=[CONSUMPTION],
            raw=[{"basal": 1907.5, "exercise": 365, "intake": 0}],
            notes=[],
        )

    def sleep(self, start, end):
        self.calls.append(("sleep", start, end, {}))
        return DomainResult(records=[SLEEP], raw=[{"total_duration": 60}], notes=[])

    def heart_rate_daily(self, start, end):
        self.calls.append(("heart_rate_daily", start, end, {}))
        return DomainResult(records=[HR_DAY], raw=[{"avg_hr": 70}], notes=[])

    def spo2_daily(self, start, end):
        self.calls.append(("spo2_daily", start, end, {}))
        return DomainResult(records=[SPO2_DAY], raw=[{"avg_spo2": 96}], notes=[])

    def stress_daily(self, start, end):
        self.calls.append(("stress_daily", start, end, {}))
        return DomainResult(records=[STRESS_DAY], raw=[{"avg_stress": 35}], notes=[])

    def spo2(self, start, end):
        self.calls.append(("spo2", start, end, {}))
        return DomainResult(
            records=[SPO2_POINT], raw=[{"time": 1790812860, "spo2": 97}], notes=[]
        )

    def weight(self, start, end):
        self.calls.append(("weight", start, end, {}))
        return DomainResult(
            records=[WEIGHT], raw=[{"time": 1790812800, "weight": 70.5}], notes=[]
        )

    def workouts(self, start, end):
        self.calls.append(("workouts", start, end, {}))
        return DomainResult(records=[WORKOUT], raw=[{"sport_type": 1}], notes=[])

    def workout_detail(self, start, end, *, track=True):
        self.calls.append(("workout_detail", start, end, {"track": track}))
        return DomainResult(
            records=[DETAIL],
            raw=[{"workout": {"sport_type": 1}, "record": None, "gps": None}],
            notes=[],
        )

    def heart_rate(self, start, end, *, full=False):
        self.calls.append(("heart_rate", start, end, {"full": full}))
        return DomainResult(
            records=[HEART_RATE_POINT],
            raw=[{"time": 1790812860, "bpm": 71}],
            notes=["Window query: result may be incomplete."],
        )

    def profile(self):
        self.calls.append(("profile",))
        return DomainResult(records=[PROFILE], raw=[{"height": 175}], notes=["n1"])

    def raw_window(self, key, start, end):
        self.calls.append(("raw_window", key, start, end, {}))
        return DomainResult(records=[], raw=[ROW_DECODED], notes=[])

    def daily_report(self, key, start, end, *, tag="daily_report"):
        self.calls.append(("daily_report", key, start, end, {"tag": tag}))
        return DomainResult(records=[], raw=[ROW_DECODED], notes=[])


@pytest.fixture
def fake_mifit(monkeypatch):
    FakeMiFit.instances = []
    FakeMiFit.error_on_init = None
    monkeypatch.setattr(cli, "MiFit", FakeMiFit)
    return FakeMiFit


class LoginRecorder:
    """Patches ``login_qr``/``save_session`` and records how they were used."""

    def __init__(self, monkeypatch, *, error=None, show_qr=True):
        self.saved = []
        self.login_kwargs = []
        self.session = make_session()
        self.error = error
        self.show_qr = show_qr
        monkeypatch.setattr(cli, "login_qr", self._login_qr)
        monkeypatch.setattr(cli, "save_session", self._save_session)

    def _login_qr(self, http=None, on_qr=None):
        self.login_kwargs.append({"http": http, "on_qr": on_qr})
        if on_qr is not None and self.show_qr:
            on_qr(LOGIN_URL, QR_IMAGE_URL)
        if self.error is not None:
            raise self.error
        return self.session

    def _save_session(self, session, path=None):
        self.saved.append(session)
        return Path("synthetic-home") / ".mifit" / "session.json"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_steps_writes_json(fake_mifit, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"records", "raw", "notes"}
    assert data["records"] == [asdict(STEP)]
    assert data["raw"] == [{"time": 1790812800, "steps": 4321}]
    assert data["notes"] == []
    assert fake_mifit.instances[0].calls == [("steps", "2026-10-01", "2026-10-02", {})]
    # A file export keeps stdout for a short summary, never the data itself.
    assert "4321" not in capsys.readouterr().out


def test_export_consumption_dispatches_to_the_client(fake_mifit, tmp_path):
    out = tmp_path / "consumption.json"

    rc = cli.main(
        ["export", "consumption", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(CONSUMPTION)]
    assert fake_mifit.instances[0].calls == [("consumption", "2026-10-01", "2026-10-02", {})]


def test_export_sleep_writes_nested_records(fake_mifit, tmp_path):
    out = tmp_path / "sleep.json"

    rc = cli.main(
        ["export", "sleep", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(SLEEP)]
    assert data["records"][0]["sessions"][0]["stages"][0]["stage"] == "deep"
    assert fake_mifit.instances[0].calls == [("sleep", "2026-10-01", "2026-10-02", {})]


def test_export_heart_rate_daily_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "heart_rate_daily.json"

    rc = cli.main(
        [
            "export",
            "heart_rate_daily",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(HR_DAY)]
    assert fake_mifit.instances[0].calls == [("heart_rate_daily", "2026-10-01", "2026-10-02", {})]


def test_export_spo2_daily_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "spo2_daily.json"

    rc = cli.main(
        [
            "export",
            "spo2_daily",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(SPO2_DAY)]
    assert fake_mifit.instances[0].calls == [("spo2_daily", "2026-10-01", "2026-10-02", {})]


def test_export_stress_daily_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "stress_daily.json"

    rc = cli.main(
        [
            "export",
            "stress_daily",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(STRESS_DAY)]
    assert fake_mifit.instances[0].calls == [("stress_daily", "2026-10-01", "2026-10-02", {})]


def test_export_spo2_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "spo2.json"

    rc = cli.main(
        ["export", "spo2", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(SPO2_POINT)]
    assert data["raw"] == [{"time": 1790812860, "spo2": 97}]
    assert fake_mifit.instances[0].calls == [("spo2", "2026-10-01", "2026-10-02", {})]


def test_export_weight_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "weight.json"

    rc = cli.main(
        ["export", "weight", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(WEIGHT)]
    assert data["raw"] == [{"time": 1790812800, "weight": 70.5}]
    assert fake_mifit.instances[0].calls == [("weight", "2026-10-01", "2026-10-02", {})]


def test_export_workouts_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "workouts.json"

    rc = cli.main(
        [
            "export",
            "workouts",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(WORKOUT)]
    assert fake_mifit.instances[0].calls == [("workouts", "2026-10-01", "2026-10-02", {})]


def test_export_workout_detail_writes_nested_records(fake_mifit, tmp_path):
    out = tmp_path / "detail.json"

    rc = cli.main(
        [
            "export",
            "workout_detail",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(DETAIL)]
    assert data["raw"] == [{"workout": {"sport_type": 1}, "record": None, "gps": None}]
    assert fake_mifit.instances[0].calls == [
        ("workout_detail", "2026-10-01", "2026-10-02", {"track": True})
    ]


def test_export_workout_detail_no_track(fake_mifit, capsys):
    rc = cli.main(
        ["export", "workout_detail", "--from", "2026-10-01", "--to", "2026-10-02", "--no-track"]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("workout_detail", "2026-10-01", "2026-10-02", {"track": False})
    ]
    assert "--no-track" not in capsys.readouterr().err  # it applies here: no "ignored" note


def test_no_track_is_ignored_for_other_domains(fake_mifit, capsys):
    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--no-track"])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("steps", "2026-10-01", "2026-10-02", {})]
    assert (
        "note: --no-track only affects workout_detail; ignored for steps."
        in capsys.readouterr().err
    )


def test_export_unknown_domain_errors(fake_mifit, capsys):
    rc = cli.main(["export", "no_such_domain", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    captured = capsys.readouterr()
    message = captured.out + captured.err
    assert "no_such_domain" in message
    for domain in REGISTRY:
        assert domain in message
    assert "steps" in message and "heart_rate" in message
    assert fake_mifit.instances == []


def test_export_without_json_prints_json_to_stdout(fake_mifit, capsys):
    rc = cli.main(["export", "heart_rate", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)  # stdout is the JSON and nothing else
    assert data["records"] == [asdict(HEART_RATE_POINT)]
    assert data["notes"] == ["Window query: result may be incomplete."]
    # Caveats are still shown to the person running the command, on stderr.
    assert "result may be incomplete" in captured.err


def test_export_heart_rate_full_flag(fake_mifit):
    assert cli.main(["export", "heart_rate", "--from", "2026-10-01", "--to", "2026-10-02"]) == 0
    assert (
        cli.main(["export", "heart_rate", "--from", "2026-10-01", "--to", "2026-10-02", "--full"])
        == 0
    )

    first, second = (instance.calls[0] for instance in fake_mifit.instances)
    assert first == ("heart_rate", "2026-10-01", "2026-10-02", {"full": False})
    assert second == ("heart_rate", "2026-10-01", "2026-10-02", {"full": True})


def test_export_full_flag_does_not_affect_steps(fake_mifit, capsys):
    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--full"])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("steps", "2026-10-01", "2026-10-02", {})]
    assert "--full" in capsys.readouterr().err  # said that it was ignored


def test_export_full_flag_is_ignored_for_heart_rate_daily(fake_mifit, capsys):
    rc = cli.main(
        ["export", "heart_rate_daily", "--from", "2026-10-01", "--to", "2026-10-02", "--full"]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("heart_rate_daily", "2026-10-01", "2026-10-02", {})]
    assert "--full" in capsys.readouterr().err  # said that it was ignored


def test_export_full_flag_is_ignored_for_workouts(fake_mifit, capsys):
    rc = cli.main(["export", "workouts", "--from", "2026-10-01", "--to", "2026-10-02", "--full"])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("workouts", "2026-10-01", "2026-10-02", {})]
    assert "--full" in capsys.readouterr().err  # said that it was ignored


@pytest.mark.parametrize(
    "dates",
    [
        ["--from", "2026-13-01", "--to", "2026-10-02"],
        ["--from", "2026-10-01", "--to", "yesterday"],
        ["--from", "2026-10-03", "--to", "2026-10-02"],
        # Its first second exists on some platforms, its last never does.
        ["--from", "9999-12-31", "--to", "9999-12-31"],
    ],
    ids=["bad-month", "not-a-date", "from-after-to", "end-of-day-unrepresentable"],
)
def test_export_rejects_bad_dates(fake_mifit, dates, capsys):
    rc = cli.main(["export", "steps", *dates])

    assert rc != 0
    assert capsys.readouterr().err  # a message, not a traceback
    assert fake_mifit.instances == []


@pytest.mark.parametrize("missing", ["--from", "--to"])
def test_export_requires_both_dates(fake_mifit, missing):
    args = {"--from": "2026-10-01", "--to": "2026-10-02"}
    del args[missing]

    rc = cli.main(["export", "steps", *(part for pair in args.items() for part in pair)])

    assert rc != 0
    assert fake_mifit.instances == []


def test_export_date_must_have_a_representable_end_of_day(fake_mifit, monkeypatch, capsys):
    """A day whose last second the platform cannot represent is refused by argparse,
    not left to fail in the middle of a read."""
    real = cli.to_epoch_s

    def end_unrepresentable(value, *, end_of_day=False, tz=None):
        if value == "2026-10-02" and end_of_day:
            raise ValueError(f"{value!r} is not a supported calendar date")
        return real(value, end_of_day=end_of_day, tz=tz)

    monkeypatch.setattr(cli, "to_epoch_s", end_unrepresentable)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc == 2
    assert "'2026-10-02' is not a supported calendar date" in capsys.readouterr().err
    assert fake_mifit.instances == []


def test_export_without_session_points_to_login(fake_mifit, capsys):
    fake_mifit.error_on_init = AuthError("No valid Mi Fitness login session was found.")

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "No valid Mi Fitness login session" in err
    assert "mifit login" in err


def test_export_auth_error_prints_session_age(fake_mifit, monkeypatch, capsys):
    def refused(self, start, end):
        raise AuthError("The Mi Fitness service rejected the session token.")

    monkeypatch.setattr(FakeMiFit, "steps", refused)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    captured = capsys.readouterr()
    err = captured.err
    assert "error: The Mi Fitness service rejected the session token." in err
    assert "session: logged in 2026-10-14T00:00:00Z" in err
    assert "never renewed" in err
    assert err.index("error:") < err.index("session: logged in") < err.index("Run `mifit login`")
    for secret in (SYNTH_TOKEN, SYNTH_SSECURITY, SYNTH_COOKIE):
        assert secret not in captured.out + err


def test_export_without_session_prints_no_session_line(fake_mifit, capsys):
    fake_mifit.error_on_init = AuthError("No valid Mi Fitness login session was found.")

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "No valid Mi Fitness login session" in err
    assert "session:" not in err
    assert "Run `mifit login`" in err


def test_open_client_value_error_leaves_no_chain(fake_mifit):
    """The session loader's ``ValueError`` is replaced by an ``AuthError`` that
    carries nothing from it (not ``__cause__``, not ``__context__``)."""
    fake_mifit.error_on_init = ValueError("session file x is not valid JSON")

    with pytest.raises(AuthError) as excinfo:
        cli._open_client()

    assert exception_chain(excinfo.value) == [excinfo.value]


def test_export_api_error_is_reported_without_traceback(fake_mifit, monkeypatch, capsys):
    def boom(self, start, end):
        raise ApiError("Request refused (code 401).", code=401)

    monkeypatch.setattr(FakeMiFit, "steps", boom)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    assert "Request refused (code 401)." in capsys.readouterr().err


def test_export_network_error_does_not_echo_the_exception_text(fake_mifit, monkeypatch, capsys):
    import requests

    def boom(self, start, end):
        # requests puts the full URL (which can carry credentials) in the message.
        raise requests.ConnectionError("https://host.example/path?token=SECRET-IN-URL")

    monkeypatch.setattr(FakeMiFit, "steps", boom)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "SECRET-IN-URL" not in captured.out + captured.err
    assert "ConnectionError" in captured.err


class Unauthorized:
    """A health-API ``http`` that refuses every request with HTTP 401."""

    def get(self, url, **kwargs):
        return SimpleNamespace(status_code=401, text="")


def test_export_renewal_network_error_does_not_echo_the_sts_url(monkeypatch, capsys):
    """The real ``MiFit`` and ``Transport`` on the stored session (under the isolated
    home); only the health API and the renewal's network call are faked."""
    from mifit import client as client_module
    from mifit import transport as transport_module
    from mifit.client import MiFit

    client_sign = "SYNTH-CLIENT-SIGN-CLI-0001"
    sts_url = "https://sts.synthetic.example.com/sts?d=synthetic-cli&ticket=0"

    def renew_fails_on_the_network(session, http=None):
        # What requests raises when the STS call cannot connect: the URL, with its
        # one-time ticket and clientSign, is in the message.
        raise requests.ConnectionError(
            f"Max retries exceeded with url: {sts_url}&clientSign={client_sign}"
        )

    monkeypatch.setattr(client_module, "renew_session", renew_fails_on_the_network)
    monkeypatch.setattr(transport_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli, "MiFit", lambda: MiFit(http=Unauthorized()))
    save_session(dataclasses.replace(make_session(), pass_token="SYNTH-PASS-TOKEN-CLI-0001"))

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-01"])

    assert rc != 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    # A network failure, reported as one: logging in again over a dead network
    # would not help, so there is no re-login advice.
    assert "error: network request failed (ConnectionError)." in captured.err
    assert "mifit login" not in output
    assert "could not be renewed" not in output
    for secret in (client_sign, "sts.synthetic.example.com", "SYNTH-PASS-TOKEN-CLI-0001"):
        assert secret not in output
    log = default_session_log_path().read_text(encoding="utf-8")
    assert [json.loads(line)["event"] for line in log.splitlines()] == [
        "unauthorized",
        "renew_network_error",
    ]
    assert client_sign not in log and "sts.synthetic" not in log


def test_export_unwritable_json_path_is_reported(fake_mifit, tmp_path, capsys):
    missing_dir = tmp_path / "no-such-dir" / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(missing_dir)]
    )

    assert rc != 0
    assert str(missing_dir) in capsys.readouterr().err


def _reject(constant):
    pytest.fail(f"non-standard JSON constant in the output: {constant}")


NON_FINITE_NOTE = (
    "Replaced 3 non-finite number(s) (NaN or Infinity) with null: "
    "strict JSON has no such values."
)


def _patch_non_finite_steps(monkeypatch):
    def steps(self, start, end):
        self.calls.append(("steps", start, end, {}))
        return DomainResult(
            records=[STEP],
            raw=[{"a": float("nan"), "b": [float("inf"), float("-inf"), 1.5]}],
            notes=[],
        )

    monkeypatch.setattr(FakeMiFit, "steps", steps)


def test_export_replaces_non_finite_numbers_with_null(fake_mifit, monkeypatch, tmp_path, capsys):
    _patch_non_finite_steps(monkeypatch)
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    data = json.loads(text, parse_constant=_reject)  # strict: no NaN / Infinity literals
    assert data["raw"] == [{"a": None, "b": [None, None, 1.5]}]
    assert data["notes"][-1] == NON_FINITE_NOTE
    assert f"note: {NON_FINITE_NOTE}" in capsys.readouterr().err


def test_export_non_finite_record_field_becomes_null(fake_mifit, monkeypatch, tmp_path):
    nan_record = dataclasses.replace(CONSUMPTION, resting_kcal=float("nan"))

    def consumption(self, start, end):
        return DomainResult(records=[nan_record], raw=[], notes=[])

    monkeypatch.setattr(FakeMiFit, "consumption", consumption)
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "consumption", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"), parse_constant=_reject)
    assert data["records"][0]["resting_kcal"] is None
    assert data["records"][0]["active_kcal"] == 365.0
    assert data["notes"] == [
        "Replaced 1 non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
    ]


def test_export_stdout_is_strict_json(fake_mifit, monkeypatch, capsys):
    _patch_non_finite_steps(monkeypatch)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out, parse_constant=_reject)
    assert data["raw"] == [{"a": None, "b": [None, None, 1.5]}]
    assert data["notes"][-1] == NON_FINITE_NOTE


def test_export_finite_output_has_no_replacement_note(fake_mifit, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["notes"] == []
    assert "Replaced" not in capsys.readouterr().err


def test_finite_replaces_only_non_finite_floats_at_any_depth():
    value = {
        "nan": float("nan"),
        "rows": [(float("inf"), {"deep": [float("-inf")]}), 2.5, 3, True, None, "x"],
    }

    cleaned, count = cli._finite(value)

    assert count == 3
    assert cleaned == {"nan": None, "rows": [[None, {"deep": [None]}], 2.5, 3, True, None, "x"]}
    assert isinstance(cleaned["rows"][0], list)  # tuples become lists
    assert cleaned["rows"][4] is None and cleaned["rows"][3] is True
    assert cli._finite([1, 2.0, {"k": "v"}]) == ([1, 2.0, {"k": "v"}], 0)
    assert value["nan"] != value["nan"]  # the input is left untouched


def test_finite_keeps_the_order_of_keys_and_items():
    value = {
        "b": [3, float("nan"), {"z": 1, "a": 2}],
        "a": (1.5, "x"),
        "c": {"y": None, "x": float("inf")},
    }

    cleaned, count = cli._finite(value)

    assert count == 2
    # ``json.dumps`` writes keys in insertion order, so this pins the order too.
    assert json.dumps(cleaned) == (
        '{"b": [3, null, {"z": 1, "a": 2}], "a": [1.5, "x"], "c": {"y": null, "x": null}}'
    )


def test_finite_handles_nesting_deeper_than_the_recursion_limit():
    depth = 5000  # json.loads accepts this deep on Python 3.14
    value = float("nan")
    for level in range(depth):
        value = [value] if level % 2 else {"k": value}

    cleaned, count = cli._finite(value)

    assert count == 1
    # Walked down in a loop: ``==`` on 5000 levels would recurse itself.
    node = cleaned
    for level in reversed(range(depth)):
        if level % 2:
            assert isinstance(node, list) and len(node) == 1
            node = node[0]
        else:
            assert isinstance(node, dict) and list(node) == ["k"]
            node = node["k"]
    assert node is None


# ---------------------------------------------------------------------------
# chunked export
# ---------------------------------------------------------------------------

# 74 days: three month chunks counted from --from, read newest first.
RANGE = ["--from", "2026-01-01", "--to", "2026-03-15"]
LABELS = ["2026-03-04..2026-03-15", "2026-02-01..2026-03-03", "2026-01-01..2026-01-31"]
HEADER = {
    "mifit_partial": 1,
    "domain": "steps",
    "from": "2026-01-01",
    "to": "2026-03-15",
    "chunk": "month",
}
RESUME_HINT = "Completed chunks are saved in {}; rerun the same command with --resume to continue."
NO_CHUNK_HINT = "No chunk is saved in {} yet; rerun the same command with --resume to continue."


def echo(fail_at=None, error=None, domain="steps"):
    """A ``FakeMiFit`` domain method: it records its call, raises ``error`` for the
    window that starts at ``fail_at``, and otherwise returns one record, one raw row
    and one note naming its window."""

    def read(self, start, end):
        self.calls.append((domain, start, end, {}))
        if start == fail_at:
            raise error
        return DomainResult(
            records=[{"chunk": f"{start}..{end}"}], raw=[{"from": start}], notes=[f"read {start}"]
        )

    return read


def export_steps(out, *flags):
    """Export steps over ``RANGE`` (three chunks) to ``out``."""
    return cli.main(["export", "steps", *RANGE, "--json", str(out), *flags])


def partial_of(out):
    return Path(f"{out}.partial")


def stored_lines(out):
    """The lines of ``out``'s partial file, parsed."""
    return [json.loads(line) for line in partial_of(out).read_bytes().splitlines()]


def stored_labels(out):
    return [line["chunk"] for line in stored_lines(out)[1:]]


def fail_second_chunk(monkeypatch, out, error):
    """Run the three-chunk export to ``out`` with ``error`` raised at its second chunk."""
    monkeypatch.setattr(FakeMiFit, "steps", echo(fail_at="2026-02-01", error=error))
    return export_steps(out)


def uninterrupted_output(monkeypatch, tmp_path):
    """The output file of the three-chunk export run in one go, in a directory of its own."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    assert export_steps(fresh / "out.json") == 0
    return (fresh / "out.json").read_text(encoding="utf-8")


def test_short_range_is_one_call_as_before(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    # Only these two write a partial file: none may exist at any point of the run.
    monkeypatch.setattr(cli, "start_partial", lambda *args: pytest.fail("partial created"))
    monkeypatch.setattr(cli, "append_part", lambda *args: pytest.fail("partial appended"))

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("steps", "2026-10-01", "2026-10-02", {})]
    assert not partial_of(out).exists()
    assert "chunk" not in capsys.readouterr().err
    assert json.loads(out.read_text(encoding="utf-8"))["notes"] == ["read 2026-10-01"]


def test_multi_chunk_export_reads_each_chunk_newest_first(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = export_steps(out)

    assert rc == 0
    assert len(fake_mifit.instances) == 1  # one client for the whole export
    assert fake_mifit.instances[0].calls == [
        ("steps", "2026-03-04", "2026-03-15", {}),
        ("steps", "2026-02-01", "2026-03-03", {}),
        ("steps", "2026-01-01", "2026-01-31", {}),
    ]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [{"chunk": label} for label in LABELS]
    assert data["raw"] == [{"from": "2026-03-04"}, {"from": "2026-02-01"}, {"from": "2026-01-01"}]
    assert data["notes"][0] == "2026-03-04..2026-03-15: read 2026-03-04"
    err = capsys.readouterr().err
    assert "export: 3 chunks of up to 31 days, read one at a time." in err
    assert "chunk 1/3 2026-03-04..2026-03-15: 1 record(s)" in err
    assert "chunk 3/3 2026-01-01..2026-01-31: 1 record(s)" in err
    assert not partial_of(out).exists()


def test_consumption_chunks_run_oldest_first(fake_mifit, monkeypatch, tmp_path):
    monkeypatch.setattr(FakeMiFit, "consumption", echo(domain="consumption"))

    rc = cli.main(["export", "consumption", *RANGE, "--json", str(tmp_path / "out.json")])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("consumption", "2026-01-01", "2026-01-31", {}),
        ("consumption", "2026-02-01", "2026-03-03", {}),
        ("consumption", "2026-03-04", "2026-03-15", {}),
    ]


def test_heart_rate_long_range_is_read_in_week_chunks(fake_mifit, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "heart_rate", "--from", "2026-10-01", "--to", "2026-10-14", "--json", str(out)]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("heart_rate", "2026-10-08", "2026-10-14", {"full": False}),
        ("heart_rate", "2026-10-01", "2026-10-07", {"full": False}),
    ]
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["records"] == [asdict(HEART_RATE_POINT)] * 2
    assert data["notes"] == [
        "2026-10-08..2026-10-14: Window query: result may be incomplete.",
        "2026-10-01..2026-10-07: Window query: result may be incomplete.",
    ]
    assert "export: 2 chunks of up to 7 days, read one at a time." in capsys.readouterr().err
    assert not partial_of(out).exists()


def test_chunk_flag_overrides_domain_default(fake_mifit, monkeypatch, tmp_path):
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = cli.main(
        [
            "export",
            "steps",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-14",
            "--chunk",
            "week",
            "--json",
            str(tmp_path / "out.json"),
        ]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("steps", "2026-10-08", "2026-10-14", {}),
        ("steps", "2026-10-01", "2026-10-07", {}),
    ]


def test_day_chunks_are_announced_in_the_singular(fake_mifit, monkeypatch, capsys):
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--chunk", "day"]
    )

    assert rc == 0
    assert len(fake_mifit.instances[0].calls) == 2
    assert "export: 2 chunks of up to 1 day, read one at a time." in capsys.readouterr().err


def test_long_range_needs_long_flag(fake_mifit, capsys):
    rc = cli.main(["export", "steps", "--from", "2026-01-01", "--to", "2026-04-03"])  # 93 days

    assert rc == 2
    err = capsys.readouterr().err
    assert "93 days" in err and "92" in err and "--long" in err
    assert (
        "mifit: error: export: the range is 93 days long; a range over 92 days "
        "takes many requests and needs --long\n"
    ) in err
    assert fake_mifit.instances == []


def test_92_days_need_no_flag(fake_mifit, monkeypatch, capsys):
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    assert cli.main(["export", "steps", "--from", "2026-01-01", "--to", "2026-04-02"]) == 0


def test_long_flag_runs_a_long_range(fake_mifit, monkeypatch, capsys):
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = cli.main(["export", "steps", "--from", "2026-01-01", "--to", "2026-04-03", "--long"])

    assert rc == 0
    assert len(fake_mifit.instances[0].calls) == 3


def test_failed_chunk_keeps_completed_chunks_and_points_to_resume(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"

    rc = fail_second_chunk(monkeypatch, out, ApiError("boom"))

    assert rc == 1
    err = capsys.readouterr().err
    assert "error: boom (chunk 2/3 2026-02-01..2026-03-03)" in err
    assert RESUME_HINT.format(partial_of(out)) in err
    assert not out.exists()
    assert stored_lines(out) == [
        HEADER,
        {
            "chunk": "2026-03-04..2026-03-15",
            "records": [{"chunk": "2026-03-04..2026-03-15"}],
            "raw": [{"from": "2026-03-04"}],
            "notes": ["read 2026-03-04"],
        },
    ]


def test_resume_reads_only_remaining_chunks(fake_mifit, monkeypatch, tmp_path, capsys):
    expected = uninterrupted_output(monkeypatch, tmp_path)
    out = tmp_path / "out.json"
    assert fail_second_chunk(monkeypatch, out, ApiError("boom")) == 1
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    capsys.readouterr()

    rc = export_steps(out, "--resume")

    assert rc == 0
    assert fake_mifit.instances[-1].calls == [
        ("steps", "2026-02-01", "2026-03-03", {}),
        ("steps", "2026-01-01", "2026-01-31", {}),
    ]
    assert out.read_text(encoding="utf-8") == expected
    assert not partial_of(out).exists()
    err = capsys.readouterr().err
    assert f"resuming {partial_of(out)}: 1 of 3 chunk(s) already done" in err
    assert "chunk 2/3" in err and "chunk 1/3" not in err


def test_resume_with_every_chunk_done_sends_no_request(fake_mifit, monkeypatch, tmp_path):
    expected = uninterrupted_output(monkeypatch, tmp_path)
    out = tmp_path / "out.json"
    start_partial(partial_of(out), HEADER)
    for chunk in plan_chunks("steps", date(2026, 1, 1), date(2026, 3, 15)):
        first = chunk.first.isoformat()
        part = DomainResult(
            records=[{"chunk": chunk.label}], raw=[{"from": first}], notes=[f"read {first}"]
        )
        append_part(partial_of(out), chunk.label, part)
    fake_mifit.instances.clear()

    rc = export_steps(out, "--resume")

    assert rc == 0
    assert fake_mifit.instances == []  # nothing is left to read, so no client is opened
    assert out.read_text(encoding="utf-8") == expected
    assert not partial_of(out).exists()


def test_existing_partial_without_resume_is_refused(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    assert fail_second_chunk(monkeypatch, out, ApiError("boom")) == 1
    before = partial_of(out).read_bytes()
    fake_mifit.instances.clear()
    capsys.readouterr()

    rc = export_steps(out)

    assert rc == 1
    assert (
        f"error: {partial_of(out)} holds an unfinished export; rerun with --resume to "
        "continue it, or delete it to start over."
    ) in capsys.readouterr().err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == before


def test_resume_with_different_arguments_is_refused(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    assert fail_second_chunk(monkeypatch, out, ApiError("boom")) == 1
    before = partial_of(out).read_bytes()
    fake_mifit.instances.clear()
    capsys.readouterr()

    rc = cli.main(
        [
            "export",
            "steps",
            "--from",
            "2026-01-01",
            "--to",
            "2026-03-20",
            "--json",
            str(out),
            "--resume",
        ]
    )

    assert rc == 1
    assert "2026-03-15" in capsys.readouterr().err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == before


DETAIL_RANGE = ["--from", "2026-10-01", "--to", "2026-10-14"]


def detail_echo(fail_at=None, error=None):
    """``echo`` for ``FakeMiFit.workout_detail``: the call also records its ``track``."""

    def read(self, start, end, *, track=True):
        self.calls.append(("workout_detail", start, end, {"track": track}))
        if start == fail_at:
            raise error
        return DomainResult(
            records=[{"chunk": f"{start}..{end}"}], raw=[{"from": start}], notes=[f"read {start}"]
        )

    return read


def export_workout_detail(out, *flags):
    """Export workout_detail over ``DETAIL_RANGE`` (two week chunks) to ``out``."""
    return cli.main(["export", "workout_detail", *DETAIL_RANGE, "--json", str(out), *flags])


def test_chunked_workout_detail_passes_track_to_every_chunk(fake_mifit, monkeypatch, tmp_path):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "workout_detail", detail_echo())

    rc = export_workout_detail(out, "--no-track")

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("workout_detail", "2026-10-08", "2026-10-14", {"track": False}),
        ("workout_detail", "2026-10-01", "2026-10-07", {"track": False}),
    ]
    assert not partial_of(out).exists()


def test_partial_header_records_the_track_setting(fake_mifit, monkeypatch, tmp_path):
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        FakeMiFit, "workout_detail", detail_echo(fail_at="2026-10-01", error=ApiError("boom"))
    )

    rc = export_workout_detail(out, "--no-track")

    assert rc == 1
    first_line = partial_of(out).read_bytes().splitlines()[0]
    assert json.loads(first_line) == {
        "mifit_partial": 1,
        "domain": "workout_detail",
        "from": "2026-10-01",
        "to": "2026-10-14",
        "chunk": "week",
        "track": False,
    }
    assert first_line == (
        b'{"mifit_partial":1,"domain":"workout_detail","from":"2026-10-01",'
        b'"to":"2026-10-14","chunk":"week","track":false}'
    )
    assert stored_labels(out) == ["2026-10-08..2026-10-14"]


def test_resume_with_a_different_track_setting_is_refused(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        FakeMiFit, "workout_detail", detail_echo(fail_at="2026-10-01", error=ApiError("boom"))
    )
    assert export_workout_detail(out, "--no-track") == 1
    before = partial_of(out).read_bytes()
    fake_mifit.instances.clear()
    capsys.readouterr()

    rc = export_workout_detail(out, "--resume")

    assert rc == 1
    err = capsys.readouterr().err
    assert "track false" in err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == before


def test_default_export_records_track_true(fake_mifit, monkeypatch, tmp_path):
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        FakeMiFit, "workout_detail", detail_echo(fail_at="2026-10-01", error=ApiError("boom"))
    )

    rc = export_workout_detail(out)

    assert rc == 1
    assert fake_mifit.instances[0].calls[0][3] == {"track": True}
    first_line = partial_of(out).read_bytes().splitlines()[0]
    assert first_line == (
        b'{"mifit_partial":1,"domain":"workout_detail","from":"2026-10-01",'
        b'"to":"2026-10-14","chunk":"week","track":true}'
    )


def test_resume_with_no_track_reads_the_rest_without_track(fake_mifit, monkeypatch, tmp_path):
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        FakeMiFit, "workout_detail", detail_echo(fail_at="2026-10-01", error=ApiError("boom"))
    )
    assert export_workout_detail(out, "--no-track") == 1
    fake_mifit.instances.clear()
    monkeypatch.setattr(FakeMiFit, "workout_detail", detail_echo())

    rc = export_workout_detail(out, "--resume", "--no-track")

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("workout_detail", "2026-10-01", "2026-10-07", {"track": False})
    ]
    assert not partial_of(out).exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["records"] == [
        {"chunk": "2026-10-08..2026-10-14"},
        {"chunk": "2026-10-01..2026-10-07"},
    ]


def test_resume_of_a_track_export_with_no_track_is_refused(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(
        FakeMiFit, "workout_detail", detail_echo(fail_at="2026-10-01", error=ApiError("boom"))
    )
    assert export_workout_detail(out) == 1
    before = partial_of(out).read_bytes()
    fake_mifit.instances.clear()
    capsys.readouterr()

    rc = export_workout_detail(out, "--resume", "--no-track")

    assert rc == 1
    err = capsys.readouterr().err
    assert "belongs to another export" in err
    assert "track true" in err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == before


def test_resume_of_a_header_without_track_says_another_version(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    header = (
        b'{"mifit_partial":1,"domain":"workout_detail","from":"2026-10-01",'
        b'"to":"2026-10-14","chunk":"week"}\n'
    )
    chunk = b'{"chunk":"2026-10-08..2026-10-14","records":[],"raw":[],"notes":[]}\n'
    partial_of(out).write_bytes(header + chunk)
    monkeypatch.setattr(FakeMiFit, "workout_detail", detail_echo())

    rc = export_workout_detail(out, "--resume", "--no-track")

    assert rc == 1
    err = capsys.readouterr().err
    assert f"{partial_of(out)} was written by another version of mifit" in err
    assert "another export" not in err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == header + chunk


def test_resume_without_partial_starts_fresh(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = export_steps(out, "--resume")

    assert rc == 0
    assert (
        f"nothing to resume at {partial_of(out)}; starting from the first chunk"
        in capsys.readouterr().err
    )
    assert len(fake_mifit.instances[0].calls) == 3
    assert not partial_of(out).exists()


def test_resume_is_a_no_op_for_a_range_in_one_chunk(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = cli.main(
        [
            "export",
            "steps",
            "--from",
            "2026-10-01",
            "--to",
            "2026-10-02",
            "--json",
            str(out),
            "--resume",
        ]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [("steps", "2026-10-01", "2026-10-02", {})]
    assert "resum" not in capsys.readouterr().err


def test_resume_needs_json(fake_mifit, capsys):
    rc = cli.main(["export", "steps", *RANGE, "--resume"])

    assert rc == 2
    assert "--json" in capsys.readouterr().err
    assert fake_mifit.instances == []


@pytest.mark.parametrize(
    "flags",
    [["--long"], ["--chunk", "week"], ["--resume", "--json", "x"]],
    ids=["long", "chunk", "resume"],
)
def test_full_cannot_be_combined_with_chunking_flags(fake_mifit, flags, capsys):
    rc = cli.main(
        ["export", "heart_rate", "--from", "2026-10-01", "--to", "2026-10-02", "--full", *flags]
    )

    assert rc == 2
    assert "--full" in capsys.readouterr().err
    assert fake_mifit.instances == []


def test_full_heart_rate_long_range_is_one_call(fake_mifit, capsys):
    rc = cli.main(["export", "heart_rate", "--full", "--from", "2026-01-01", "--to", "2026-07-31"])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("heart_rate", "2026-01-01", "2026-07-31", {"full": True})
    ]
    assert "chunk" not in capsys.readouterr().err


def test_auth_error_mid_export_keeps_progress(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    error = AuthError("The Mi Fitness service rejected the session token.")

    rc = fail_second_chunk(monkeypatch, out, error)

    assert rc == 1
    err = capsys.readouterr().err
    assert (
        "error: The Mi Fitness service rejected the session token. "
        "(chunk 2/3 2026-02-01..2026-03-03)"
    ) in err
    assert "session: logged in 2026-10-14T00:00:00Z" in err
    assert "Run `mifit login` to sign in." in err
    assert (
        f"Completed chunks are saved in {partial_of(out)}; after `mifit login`, "
        "rerun the same command with --resume to continue."
    ) in err
    assert stored_labels(out) == [LABELS[0]]


def test_ctrl_c_mid_export_keeps_progress(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = fail_second_chunk(monkeypatch, out, KeyboardInterrupt())

    assert rc == 130
    err = capsys.readouterr().err
    assert RESUME_HINT.format(partial_of(out)) in err
    assert err.index("--resume") < err.index("Interrupted.")
    assert stored_labels(out) == [LABELS[0]]
    assert not out.exists()


@pytest.mark.parametrize(
    ("error", "code"), [(ApiError("boom"), 1), (KeyboardInterrupt(), 130)], ids=["error", "ctrl-c"]
)
def test_resumed_partial_without_chunks_does_not_claim_any_are_saved(
    fake_mifit, monkeypatch, tmp_path, error, code, capsys
):
    out = tmp_path / "out.json"
    start_partial(partial_of(out), HEADER)  # this export's header, no chunk yet
    monkeypatch.setattr(FakeMiFit, "steps", echo(fail_at="2026-03-04", error=error))

    rc = export_steps(out, "--resume")

    assert rc == code
    err = capsys.readouterr().err
    assert "Completed chunks" not in err
    assert NO_CHUNK_HINT.format(partial_of(out)) in err
    assert stored_lines(out) == [HEADER]


def test_resumed_partial_without_chunks_after_an_auth_error_says_to_log_in(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    start_partial(partial_of(out), HEADER)
    error = AuthError("The Mi Fitness service rejected the session token.")
    monkeypatch.setattr(FakeMiFit, "steps", echo(fail_at="2026-03-04", error=error))

    assert export_steps(out, "--resume") == 1

    assert (
        f"No chunk is saved in {partial_of(out)} yet; after `mifit login`, "
        "rerun the same command with --resume to continue."
    ) in capsys.readouterr().err


def test_network_error_mid_export_names_the_chunk(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    error = requests.ConnectionError("https://host.example/path?token=SECRET-IN-URL")

    rc = fail_second_chunk(monkeypatch, out, error)

    assert rc == 1
    err = capsys.readouterr().err
    assert (
        "error: network request failed (ConnectionError) (chunk 2/3 2026-02-01..2026-03-03)."
    ) in err
    assert "SECRET-IN-URL" not in err
    assert RESUME_HINT.format(partial_of(out)) in err


def test_partial_holds_no_session_secret(fake_mifit, monkeypatch, tmp_path):
    out = tmp_path / "out.json"
    assert fail_second_chunk(monkeypatch, out, ApiError("boom")) == 1

    text = partial_of(out).read_text(encoding="ascii")

    session = make_session()
    for secret in (SYNTH_TOKEN, SYNTH_SSECURITY, SYNTH_COOKIE, session.user_id, session.c_user_id):
        assert secret not in text
    assert set(stored_lines(out)[0]) == {"mifit_partial", "domain", "from", "to", "chunk"}


def test_chunked_export_to_stdout_has_no_partial(fake_mifit, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    rc = cli.main(["export", "steps", *RANGE])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)  # stdout is the merged JSON alone
    assert data["records"] == [{"chunk": label} for label in LABELS]
    assert list(tmp_path.rglob("*.partial")) == []


def test_resumed_non_finite_chunk_matches_an_uninterrupted_run(
    fake_mifit, monkeypatch, tmp_path
):
    """A chunk is stored as it goes into the output (strict JSON, NaN as null with its
    note), so a resumed chunk is the same as a freshly read one: no second note."""

    def nan_echo(fail_at=None, error=None):
        plain = echo(fail_at, error)

        def read(self, start, end):
            result = plain(self, start, end)
            result.raw.append({"value": float("nan")})
            return result

        return read

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setattr(FakeMiFit, "steps", nan_echo())
    assert export_steps(fresh / "out.json") == 0
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", nan_echo("2026-02-01", ApiError("boom")))
    assert export_steps(out) == 1
    monkeypatch.setattr(FakeMiFit, "steps", nan_echo())

    assert export_steps(out, "--resume") == 0

    text = out.read_text(encoding="utf-8")
    assert text == (fresh / "out.json").read_text(encoding="utf-8")
    data = json.loads(text, parse_constant=_reject)
    assert [note for note in data["notes"] if "non-finite" in note] == [
        f"{label}: Replaced 1 non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
        for label in LABELS
    ]


@pytest.mark.parametrize(
    "content", [b"", b'{"mifit_partial":1,"dom'], ids=["empty", "torn-header"]
)
def test_partial_damaged_at_its_start_says_no_data_is_lost(
    fake_mifit, tmp_path, content, capsys
):
    out = tmp_path / "out.json"
    partial_of(out).write_bytes(content)

    rc = export_steps(out, "--resume")

    assert rc == 1
    err = capsys.readouterr().err
    assert f"error: {partial_of(out)} is damaged; delete it to start over" in err
    assert "It holds no finished chunk, so deleting it loses no data." in err
    assert fake_mifit.instances == []
    assert partial_of(out).read_bytes() == content


def test_damaged_partial_with_chunks_does_not_say_no_data_is_lost(
    fake_mifit, tmp_path, capsys
):
    out = tmp_path / "out.json"
    start_partial(partial_of(out), HEADER)
    with open(partial_of(out), "ab") as f:
        f.write(b"not json\n")
    append_part(partial_of(out), LABELS[0], DomainResult())

    rc = export_steps(out, "--resume")

    assert rc == 1
    err = capsys.readouterr().err
    assert "is damaged" in err
    assert "loses no data" not in err


def test_partial_that_cannot_be_read_is_a_clean_error(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"

    def unreadable(path, header, labels):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "load_partial", unreadable)

    rc = export_steps(out, "--resume")

    assert rc == 1
    assert f"error: could not load {partial_of(out)} (Permission denied)." in (
        capsys.readouterr().err
    )
    assert fake_mifit.instances == []


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (OSError(28, "No space left on device"), "No space left on device"),
        (ValueError("Out of range float values are not JSON compliant"), "ValueError"),
        (RecursionError("maximum recursion depth exceeded"), "RecursionError"),
    ],
    ids=["os-error", "value-error", "recursion-error"],
)
def test_chunk_that_cannot_be_saved_stops_the_export(
    fake_mifit, monkeypatch, tmp_path, error, detail, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    real_append = cli.append_part

    def second_append_fails(path, label, part):
        if label != LABELS[0]:
            raise error
        real_append(path, label, part)

    monkeypatch.setattr(cli, "append_part", second_append_fails)

    rc = export_steps(out)

    assert rc == 1
    err = capsys.readouterr().err
    assert (
        f"error: could not save chunk 2/3 2026-02-01..2026-03-03 to {partial_of(out)} "
        f"({detail})."
    ) in err
    assert RESUME_HINT.format(partial_of(out)) in err
    assert len(fake_mifit.instances[0].calls) == 2  # nothing is read past it
    assert stored_labels(out) == [LABELS[0]]
    assert not out.exists()


def test_partial_that_cannot_be_created_stops_the_export(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    def cannot_create(path, header):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "start_partial", cannot_create)

    rc = export_steps(out)

    assert rc == 1
    err = capsys.readouterr().err
    assert (
        f"error: could not save chunk 1/3 2026-03-04..2026-03-15 to {partial_of(out)} "
        "(Permission denied)."
    ) in err
    assert "Completed chunks" not in err  # none was kept
    assert len(fake_mifit.instances[0].calls) == 1
    assert not out.exists()


def test_first_chunk_that_cannot_be_saved_leaves_no_partial(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    def append_fails(path, label, part):
        assert path.exists()  # the header is written, the chunk is not
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli, "append_part", append_fails)

    rc = export_steps(out)

    assert rc == 1
    err = capsys.readouterr().err
    assert (
        f"error: could not save chunk 1/3 2026-03-04..2026-03-15 to {partial_of(out)} "
        "(No space left on device)."
    ) in err
    assert "--resume" not in err
    assert not partial_of(out).exists()  # it held no chunk
    monkeypatch.setattr(cli, "append_part", append_part)
    assert export_steps(out) == 0  # so the next plain run is not refused


def test_ctrl_c_before_the_first_chunk_is_saved_leaves_no_partial(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    def interrupted(path, label, part):
        assert path.exists()  # Ctrl+C lands after the header, before the chunk
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "append_part", interrupted)

    rc = export_steps(out)

    assert rc == 130
    err = capsys.readouterr().err
    assert "Interrupted." in err
    assert "--resume" not in err
    assert not partial_of(out).exists()
    monkeypatch.setattr(cli, "append_part", append_part)
    assert export_steps(out) == 0


def test_partial_left_by_a_failed_first_save_is_deleted_best_effort(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    def append_fails(path, label, part):
        raise OSError(28, "No space left on device")

    def unlink(self, missing_ok=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "append_part", append_fails)
    monkeypatch.setattr(Path, "unlink", unlink)

    rc = export_steps(out)

    assert rc == 1  # the save error is reported; the failed delete does not crash
    assert "error: could not save chunk 1/3" in capsys.readouterr().err
    assert stored_lines(out) == [HEADER]


def test_unwritable_output_keeps_the_partial_for_resume(
    fake_mifit, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out.json"
    out.mkdir()  # a directory in the way: the output cannot be written
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    assert export_steps(out) == 1
    err = capsys.readouterr().err
    assert f"error: could not write {out}" in err
    assert RESUME_HINT.format(partial_of(out)) in err
    assert stored_labels(out) == LABELS

    out.rmdir()
    fake_mifit.instances.clear()
    assert export_steps(out, "--resume") == 0
    assert fake_mifit.instances == []
    assert json.loads(out.read_text(encoding="utf-8"))["records"] == [
        {"chunk": label} for label in LABELS
    ]
    assert not partial_of(out).exists()


TOO_DEEP_ERROR = "error: the result is nested too deeply to write as JSON."


def indented_dumps_recurse(monkeypatch):
    """Make ``json.dumps`` with an ``indent`` raise ``RecursionError``, whatever the depth.

    Python 3.12 and 3.13 do this on their own for data about a thousand levels deep:
    the indenting encoder is pure Python, while the compact one (the partial file's)
    is in C. 3.14 copes with far deeper data, so the tests do not rely on it.
    """
    real_dumps = json.dumps

    def dumps(obj, *args, **kwargs):
        if kwargs.get("indent") is not None:
            raise RecursionError("maximum recursion depth exceeded")
        return real_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", dumps)


def test_output_too_deep_to_encode_is_a_clean_error(fake_mifit, monkeypatch, tmp_path, capsys):
    indented_dumps_recurse(monkeypatch)
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert TOO_DEEP_ERROR in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not out.exists()


def test_stdout_too_deep_to_encode_prints_no_json(fake_mifit, monkeypatch, capsys):
    indented_dumps_recurse(monkeypatch)

    rc = cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc == 1
    captured = capsys.readouterr()
    assert TOO_DEEP_ERROR in captured.err
    assert captured.out == ""


def test_output_too_deep_keeps_the_partial_for_resume(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    indented_dumps_recurse(monkeypatch)

    assert export_steps(out) == 1

    err = capsys.readouterr().err
    assert TOO_DEEP_ERROR in err
    assert RESUME_HINT.format(partial_of(out)) in err
    assert stored_labels(out) == LABELS
    assert not out.exists()


def test_chunk_too_deep_to_save_is_a_save_error(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())

    def too_deep(path, label, part):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(cli, "append_part", too_deep)

    assert export_steps(out) == 1

    err = capsys.readouterr().err
    assert "error: could not save chunk 1/3" in err
    assert "(RecursionError)" in err
    assert not partial_of(out).exists()


def test_partial_that_cannot_be_deleted_is_a_note(fake_mifit, monkeypatch, tmp_path, capsys):
    out = tmp_path / "out.json"
    monkeypatch.setattr(FakeMiFit, "steps", echo())
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name.endswith(".partial"):
            raise PermissionError(13, "Permission denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    rc = export_steps(out)

    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["records"]
    assert f"note: could not delete {partial_of(out)} (Permission denied)" in (
        capsys.readouterr().err
    )


def test_export_help_lists_the_chunking_flags(capsys):
    assert cli.main(["export", "--help"]) == 0

    out = capsys.readouterr().out
    for flag in ("--long", "--chunk", "--resume"):
        assert flag in out


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def test_profile_writes_records(fake_mifit, tmp_path):
    out = tmp_path / "profile.json"

    rc = cli.main(["profile", "--json", str(out)])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"records", "raw", "notes"}
    assert data["records"] == [asdict(PROFILE)]
    assert data["raw"] == [{"height": 175}]
    assert data["notes"] == ["n1"]
    assert fake_mifit.instances[0].calls == [("profile",)]


def test_profile_prints_json_to_stdout_and_notes_to_stderr(fake_mifit, capsys):
    rc = cli.main(["profile"])

    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)  # stdout is the JSON and nothing else
    assert data["records"] == [asdict(PROFILE)]
    assert data["notes"] == ["n1"]
    assert "note: n1" in captured.err


def test_profile_output_is_strict_json(fake_mifit, monkeypatch, tmp_path, capsys):
    def profile(self):
        return DomainResult(
            records=[dataclasses.replace(PROFILE, height_cm=float("nan"))],
            raw=[{"height": float("inf")}],
            notes=[],
        )

    monkeypatch.setattr(FakeMiFit, "profile", profile)
    out = tmp_path / "profile.json"

    assert cli.main(["profile", "--json", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"), parse_constant=_reject)
    assert data["records"][0]["height_cm"] is None
    assert data["raw"] == [{"height": None}]
    assert data["notes"] == [
        "Replaced 2 non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
    ]
    capsys.readouterr()

    assert cli.main(["profile"]) == 0
    json.loads(capsys.readouterr().out, parse_constant=_reject)


def test_profile_without_session_tells_to_log_in(fake_mifit, capsys):
    fake_mifit.error_on_init = AuthError("No valid Mi Fitness login session was found.")

    rc = cli.main(["profile"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "No valid Mi Fitness login session" in err
    assert "mifit login" in err


# ---------------------------------------------------------------------------
# raw
# ---------------------------------------------------------------------------


RAW_ARGS = ["raw", "stress", "--from", "2026-10-01", "--to", "2026-10-02"]


def test_raw_reads_the_window_and_writes_rows(fake_mifit, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = cli.main([*RAW_ARGS, "--json", str(out)])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("raw_window", "stress", "2026-10-01", "2026-10-02", {})
    ]
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "records": [],
        "raw": [ROW_DECODED],
        "notes": [],
    }
    # The count is of the rows (``records`` is always empty here).
    assert f"Wrote 1 row(s) to {out}." in capsys.readouterr().out


def test_raw_daily_reads_the_daily_report(fake_mifit, tmp_path):
    rc = cli.main([*RAW_ARGS, "--daily", "--json", str(tmp_path / "out.json")])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("daily_report", "stress", "2026-10-01", "2026-10-02", {"tag": "daily_report"})
    ]


def test_raw_daily_with_tag(fake_mifit, tmp_path):
    rc = cli.main(
        [*RAW_ARGS, "--daily", "--tag", "daily_fitness", "--json", str(tmp_path / "out.json")]
    )

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("daily_report", "stress", "2026-10-01", "2026-10-02", {"tag": "daily_fitness"})
    ]


def test_raw_tag_without_daily_is_a_usage_error(fake_mifit, capsys):
    rc = cli.main([*RAW_ARGS, "--tag", "daily_fitness"])

    assert rc == 2
    assert "--tag" in capsys.readouterr().err
    assert fake_mifit.instances == []


@pytest.mark.parametrize("key", ["", "   "])
def test_raw_empty_key_is_a_usage_error(fake_mifit, key, capsys):
    rc = cli.main(["raw", key, "--from", "2026-10-01", "--to", "2026-10-02"])

    assert rc == 2
    assert "KEY must not be empty" in capsys.readouterr().err
    assert fake_mifit.instances == []


@pytest.mark.parametrize("tag", ["", "   "])
def test_raw_blank_tag_is_a_usage_error(fake_mifit, tag, capsys):
    rc = cli.main([*RAW_ARGS, "--daily", "--tag", tag])

    assert rc == 2
    assert "--tag must not be empty" in capsys.readouterr().err
    assert fake_mifit.instances == []


def test_raw_long_range_needs_long(fake_mifit, tmp_path, capsys):
    long_args = ["raw", "stress", "--from", "2026-01-01", "--to", "2026-04-03"]  # 93 days

    rc = cli.main(long_args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "--long" in err
    assert "92" in err
    # One long paged read, not many chunk requests: the message says so.
    assert "paged read" in err
    assert "many requests" not in err
    assert fake_mifit.instances == []

    rc = cli.main([*long_args, "--long", "--json", str(tmp_path / "out.json")])

    assert rc == 0
    assert fake_mifit.instances[0].calls == [
        ("raw_window", "stress", "2026-01-01", "2026-04-03", {})
    ]


def test_raw_range_of_92_days_needs_no_long(fake_mifit, tmp_path):
    rc = cli.main(
        ["raw", "stress", "--from", "2026-01-01", "--to", "2026-04-02"]
        + ["--json", str(tmp_path / "out.json")]
    )

    assert rc == 0
    assert len(fake_mifit.instances[0].calls) == 1


def test_raw_rejects_a_reversed_range(fake_mifit, capsys):
    rc = cli.main(["raw", "stress", "--from", "2026-10-02", "--to", "2026-10-01"])

    assert rc == 2
    assert "--from must not be later than --to" in capsys.readouterr().err
    assert fake_mifit.instances == []


def test_raw_to_stdout_prints_only_json(fake_mifit, monkeypatch, capsys):
    def raw_window(self, key, start, end):
        return DomainResult(records=[], raw=[ROW_DECODED], notes=["n1"])

    monkeypatch.setattr(FakeMiFit, "raw_window", raw_window)

    rc = cli.main(RAW_ARGS)

    assert rc == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"records": [], "raw": [ROW_DECODED], "notes": ["n1"]}
    assert "note: n1" in captured.err


def test_raw_output_is_strict_json(fake_mifit, monkeypatch, capsys):
    def raw_window(self, key, start, end):
        return DomainResult(records=[], raw=[{"time": 1790812800, "value": float("nan")}], notes=[])

    monkeypatch.setattr(FakeMiFit, "raw_window", raw_window)

    assert cli.main(RAW_ARGS) == 0
    data = json.loads(capsys.readouterr().out, parse_constant=_reject)
    assert data["raw"] == [{"time": 1790812800, "value": None}]


def test_raw_auth_error_points_to_login(fake_mifit, monkeypatch, tmp_path, capsys):
    def raw_window(self, key, start, end):
        raise AuthError("The login session was refused.")

    monkeypatch.setattr(FakeMiFit, "raw_window", raw_window)
    out = tmp_path / "out.json"

    rc = cli.main([*RAW_ARGS, "--json", str(out)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "The login session was refused." in err
    assert "mifit login" in err
    assert not out.exists()


def test_raw_help_lists_the_flags(capsys):
    assert cli.main(["raw", "--help"]) == 0

    out = capsys.readouterr().out
    for flag in ("--from", "--to", "--daily", "--tag", "--json", "--long"):
        assert flag in out


def test_export_message_unchanged(fake_mifit, tmp_path, capsys):
    out = tmp_path / "out.json"

    rc = cli.main(
        ["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]
    )

    assert rc == 0
    assert capsys.readouterr().out == f"Wrote 1 record(s) to {out}.\n"


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


def test_cli_never_prints_token(monkeypatch, capsys):
    recorder = LoginRecorder(monkeypatch)

    rc = cli.main(["login"])

    assert rc == 0
    captured = capsys.readouterr()
    for secret in (SYNTH_TOKEN, SYNTH_SSECURITY, SYNTH_COOKIE):
        assert secret not in captured.out
        assert secret not in captured.err
    assert "<redacted>" not in captured.out  # no Session repr either
    assert recorder.saved == [recorder.session]
    assert "success" in captured.out.lower()


def test_login_records_a_login_event(monkeypatch):
    recorder = LoginRecorder(monkeypatch)
    events = []
    monkeypatch.setattr(
        cli, "record_event", lambda event, session, **kwargs: events.append((event, session))
    )

    rc = cli.main(["login"])

    assert rc == 0
    assert events == [("login", recorder.session)]


def test_login_records_no_event_when_the_login_fails(monkeypatch):
    LoginRecorder(monkeypatch, error=AuthError("The login was refused."))
    events = []
    monkeypatch.setattr(cli, "record_event", lambda *args, **kwargs: events.append(args))

    rc = cli.main(["login"])

    assert rc != 0
    assert events == []


def test_login_shows_qr_and_login_url_then_saves_session(monkeypatch, capsys):
    recorder = LoginRecorder(monkeypatch)

    rc = cli.main(["login"])

    assert rc == 0
    out = capsys.readouterr().out
    assert LOGIN_URL in out
    assert any(ch in out for ch in "█▀▄")  # block-drawn QR code
    assert callable(recorder.login_kwargs[0]["on_qr"])
    # The path the session was saved to is fine to show; its contents are not.
    assert "session.json" in out


def test_login_qr_encodes_the_login_url(monkeypatch, capsys):
    import qrcode

    added = []

    class SpyQRCode(qrcode.QRCode):
        def add_data(self, data, *args, **kwargs):
            added.append(data)
            return super().add_data(data, *args, **kwargs)

    monkeypatch.setattr(qrcode, "QRCode", SpyQRCode)
    LoginRecorder(monkeypatch)

    assert cli.main(["login"]) == 0

    # ``qr`` from the service is the address of a QR *image*; the code to scan is
    # the login URL that image stands for.
    assert added == [LOGIN_URL]
    assert QR_IMAGE_URL in capsys.readouterr().out  # offered as a fallback


def test_login_failure_reports_error_and_saves_nothing(monkeypatch, capsys):
    recorder = LoginRecorder(
        monkeypatch, error=ApiError("QR login long poll was refused (code 70016).", code=70016)
    )

    rc = cli.main(["login"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "was refused (code 70016)" in captured.err
    assert recorder.saved == []
    assert "success" not in captured.out.lower()


def test_login_security_confirmation_prints_the_url_to_open(monkeypatch, capsys):
    recorder = LoginRecorder(monkeypatch, error=NotificationRequiredError(NOTIFICATION_URL))

    rc = cli.main(["login"])

    assert rc != 0
    assert NOTIFICATION_URL in capsys.readouterr().err
    assert recorder.saved == []


def test_login_ctrl_c_exits_quietly(monkeypatch, capsys):
    recorder = LoginRecorder(monkeypatch, error=KeyboardInterrupt())

    rc = cli.main(["login"])

    assert rc == 130
    assert recorder.saved == []
    assert "Traceback" not in capsys.readouterr().err


def test_login_save_failure_is_reported_without_secrets(monkeypatch, capsys):
    LoginRecorder(monkeypatch)

    def cannot_save(session, path=None):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cli, "save_session", cannot_save)

    rc = cli.main(["login"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "Permission denied" in captured.err
    assert "success" not in captured.out.lower()
    assert SYNTH_TOKEN not in captured.out + captured.err


# ---------------------------------------------------------------------------
# QR rendering
# ---------------------------------------------------------------------------


def test_render_qr_falls_back_to_ascii_when_blocks_cannot_be_encoded():
    out = io.TextIOWrapper(io.BytesIO(), encoding="ascii", write_through=True)

    cli._render_qr(LOGIN_URL, out)

    text = out.buffer.getvalue().decode("ascii")
    assert "##" in text
    assert text.count("\n") > 10


# ---------------------------------------------------------------------------
# entry point and packaging
# ---------------------------------------------------------------------------


def test_main_help_lists_both_commands(capsys):
    rc = cli.main(["--help"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "login" in out and "export" in out


def test_main_requires_a_command(capsys):
    rc = cli.main([])

    assert rc != 0
    assert capsys.readouterr().err


def test_python_dash_m_cli_runs():
    env = {**os.environ, "PYTHONPATH": str(SRC)}

    result = subprocess.run(
        [sys.executable, "-m", "mifit.cli", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "export" in result.stdout


def test_pyproject_declares_package_dependencies_and_entry_point():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = meta["project"]

    assert project["name"] == "mifit"
    assert project["requires-python"] == ">=3.12"
    names = {
        dep.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip().lower()
        for dep in project["dependencies"]
    }
    assert names == {"requests", "qrcode", "cryptography"}
    assert "cryptography>=42" in project["dependencies"]
    assert project["scripts"] == {"mifit": "mifit.cli:main"}
    assert meta["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
