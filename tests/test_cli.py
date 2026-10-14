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
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from mifit import cli
from mifit.auth.session import Session, save_session
from mifit.paths import default_session_log_path
from mifit.domains.base import REGISTRY, DomainResult
from mifit.errors import ApiError, AuthError, NotificationRequiredError
from mifit.models.consumption import ConsumptionRecord
from mifit.models.heart_rate import HeartRateDailySummary, HeartRatePoint
from mifit.models.sleep import SleepRecord, SleepSession, SleepStage
from mifit.models.spo2 import Spo2DailySummary
from mifit.models.steps import StepRecord
from mifit.models.workouts import Workout

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
WORKOUT = Workout(
    start_epoch_s=1790820000,
    end_epoch_s=1790822701,
    utc_offset_s=28800,
    sport="running",
    source_sport_code=1,
    duration_s=2400,
    distance_m=5000,
    active_kcal=350,
    avg_hr_bpm=148,
    max_hr_bpm=170,
    steps=6000,
)


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

    def workouts(self, start, end):
        self.calls.append(("workouts", start, end, {}))
        return DomainResult(records=[WORKOUT], raw=[{"sport_type": 1}], notes=[])

    def heart_rate(self, start, end, *, full=False):
        self.calls.append(("heart_rate", start, end, {"full": full}))
        return DomainResult(
            records=[HEART_RATE_POINT],
            raw=[{"time": 1790812860, "bpm": 71}],
            notes=["Window query: result may be incomplete."],
        )


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
    ],
    ids=["bad-month", "not-a-date", "from-after-to"],
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
    assert names == {"requests", "qrcode"}
    assert project["scripts"] == {"mifit": "mifit.cli:main"}
    assert meta["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
