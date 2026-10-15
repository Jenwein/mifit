"""``mifit.mcp_server.login``: the QR login the MCP server runs in the background.

The three account requests, the session store, the session log and the local
opener are fakes; the job a login spawns is run by hand. Every URL and credential
is synthetic.
"""

import threading
import time

import pytest
import requests

from mcp_fakes import make_session
from mifit.auth.qr import QrChallenge
from mifit.errors import ApiError, NotificationRequiredError
from mifit.mcp_server.login import LOGIN_QR_FILE_NAME, LoginFlow, LoginView, qr_png
from mifit.region import SID

LOGIN_URL = "https://account.xiaomi.com/synthetic/mcp-login?t=synthetic-login"
QR_URL = "https://account.xiaomi.com/synthetic/mcp-qr?t=synthetic-qr"
LP_URL = "https://account.xiaomi.com/longPolling/synthetic?SYNTH-LP-SECRET"
NOTE_URL = "https://account.xiaomi.com/synthetic/confirm?SYNTH-NOTE-SECRET"
SESSION = make_session()

PENDING = (
    "Scan the QR code with the Xiaomi or Mi Fitness app and approve the login, or open "
    "login_url on a phone signed in to the Xiaomi account. Then call login_status."
)
SUCCEEDED = "Logged in. The session is saved on this computer; the data tools work now."
EXPIRED = "The QR code expired before the login was approved. Call login_start for a new one."
CONFIRM = (
    "Xiaomi asks for a security confirmation. A confirmation page was opened in the "
    "browser on this computer; complete it, then call login_start again."
)
CONFIRM_UNOPENED = (
    "Xiaomi asks for a security confirmation, but the page could not be opened on this "
    "computer. Run `mifit login` in a terminal instead."
)
IDLE = "No login in progress. Call login_start to log in."
UNOPENED_REMARK = " (The QR image could not be opened on this computer.)"


class Harness:
    """A ``LoginFlow`` on fakes, recording what it did."""

    def __init__(self, tmp_path, *, poll_error=None, request_error=None, save_error=None,
                 opener_result=True, request_delay=0.0):
        self.poll_error = poll_error
        self.request_error = request_error
        self.save_error = save_error
        self.opener_result = opener_result
        self.request_delay = request_delay
        self.jobs = []
        self.opened = []
        self.requests = []
        self.polls = []
        self.exchanges = []
        self.saved = []
        self.recorded = []
        self.switched = []
        self.qr_path = tmp_path / "mifit-home" / LOGIN_QR_FILE_NAME
        self.flow = LoginFlow(
            on_success=lambda: self.switched.append(True),
            qr_path=self.qr_path,
            opener=self.open,
            spawn=self.jobs.append,
            request=self.request,
            poll=self.poll,
            exchange=self.exchange,
            save=self.save,
            record=self.record,
            http_factory=object,
        )

    def open(self, target):
        self.opened.append(target)
        return self.opener_result

    def request(self, http, sid, *, device_id):
        self.requests.append((http, sid, device_id))
        time.sleep(self.request_delay)
        if self.request_error is not None:
            raise self.request_error
        return QrChallenge(login_url=LOGIN_URL, qr_payload=QR_URL, lp_url=LP_URL)

    def poll(self, challenge, http, *, device_id):
        self.polls.append((challenge, http, device_id))
        if self.poll_error is not None:
            raise self.poll_error
        return "login-result"

    def exchange(self, result, sid, http, *, device_id):
        self.exchanges.append((result, sid, http, device_id))
        return SESSION

    def save(self, session):
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(session)

    def record(self, event, session):
        self.recorded.append((event, session))

    def finish(self):
        """Run the spawned job, as the background thread would."""
        [job] = self.jobs
        job()
        return self.flow.view()


def _no_long_poll_url(view):
    for text in (repr(view), view.message, view.login_url or ""):
        assert "SYNTH-LP-SECRET" not in text


def test_qr_png_is_a_png():
    assert qr_png(LOGIN_URL).startswith(b"\x89PNG\r\n\x1a\n")


def test_nothing_started_is_idle(tmp_path):
    assert Harness(tmp_path).flow.view() == LoginView("idle", IDLE)


def test_start_shows_the_qr_and_starts_one_job(tmp_path):
    h = Harness(tmp_path)
    view = h.flow.start()
    assert view.status == "pending" and view.message == PENDING
    assert view.login_url == LOGIN_URL and view.png.startswith(b"\x89PNG")
    assert h.qr_path.read_bytes() == view.png
    assert h.opened == [h.qr_path.as_uri()]
    assert len(h.jobs) == 1
    [(http, sid, device_id)] = h.requests
    assert sid == SID and len(device_id) == 16
    _no_long_poll_url(view)


def test_a_second_start_while_pending_reuses_the_qr(tmp_path):
    h = Harness(tmp_path)
    first, second = h.flow.start(), h.flow.start()
    assert second == first
    assert len(h.requests) == 1 and len(h.jobs) == 1


def test_concurrent_starts_send_one_request(tmp_path):
    h = Harness(tmp_path, request_delay=0.05)
    views = []
    threads = [threading.Thread(target=lambda: views.append(h.flow.start())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(h.requests) == 1 and len(h.jobs) == 1
    assert views[0] == views[1] and views[0].login_url == LOGIN_URL


def test_success_saves_records_and_switches(tmp_path):
    h = Harness(tmp_path)
    h.flow.start()
    assert h.finish() == LoginView("succeeded", SUCCEEDED)
    assert h.saved == [SESSION] and h.recorded == [("login", SESSION)]
    assert h.switched == [True]
    assert not h.qr_path.exists()
    # One http client and one device id for the whole login.
    [(request_http, _, device_id)] = h.requests
    [(challenge, poll_http, poll_device)] = h.polls
    [(result, sid, exchange_http, exchange_device)] = h.exchanges
    assert request_http is poll_http is exchange_http
    assert device_id == poll_device == exchange_device
    assert challenge.lp_url == LP_URL and result == "login-result" and sid == SID


def test_confirmation_opens_the_page_locally_and_hides_the_url(tmp_path):
    h = Harness(tmp_path, poll_error=NotificationRequiredError(NOTE_URL))
    h.flow.start()
    view = h.finish()
    assert h.opened[-1] == NOTE_URL
    assert view == LoginView("confirmation_required", CONFIRM)
    assert "SYNTH-NOTE-SECRET" not in repr(view)
    assert not h.qr_path.exists() and h.saved == [] and h.switched == []


def test_confirmation_page_that_cannot_be_opened(tmp_path):
    h = Harness(tmp_path, poll_error=NotificationRequiredError(NOTE_URL))
    h.flow.start()
    h.opener_result = False
    assert h.finish() == LoginView("confirmation_required", CONFIRM_UNOPENED)


def test_long_poll_timeout_is_expired(tmp_path):
    h = Harness(tmp_path, poll_error=requests.ReadTimeout(f"timed out: {LP_URL}"))
    h.flow.start()
    view = h.finish()
    assert view == LoginView("expired", EXPIRED)
    _no_long_poll_url(view)
    assert not h.qr_path.exists()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ApiError("QR login result is missing: nonce."), "Login failed: QR login result is missing: nonce."),
        (requests.ConnectionError(f"failed: {LP_URL}"), "Login failed: network request failed (ConnectionError)."),
        (requests.ConnectTimeout(f"failed: {LP_URL}"), "Login failed: network request failed (ConnectTimeout)."),
        (RuntimeError(f"boom {LP_URL}"), "Login failed (RuntimeError)."),
    ],
    ids=["api", "network", "connect-timeout", "unexpected"],
)
def test_poll_failures(tmp_path, error, message):
    h = Harness(tmp_path, poll_error=error)
    h.flow.start()
    view = h.finish()
    assert view == LoginView("failed", message)
    _no_long_poll_url(view)
    assert not h.qr_path.exists() and h.switched == []


def test_save_failure(tmp_path):
    error = PermissionError(13, "Permission denied")
    h = Harness(tmp_path, save_error=error)
    h.flow.start()
    assert h.finish() == LoginView(
        "failed", "Login failed: could not save the session (Permission denied)."
    )
    assert h.recorded == [] and h.switched == []


@pytest.mark.parametrize("stage", ["save", "record", "on_success"])
def test_an_unexpected_error_after_the_approval_still_ends_the_login(tmp_path, capsys, stage):
    h = Harness(tmp_path)
    boom = TypeError(f"boom {LP_URL}")

    def fail(*args):
        raise boom

    if stage == "save":
        h.save = fail
    elif stage == "record":
        h.record = fail
    else:
        h.flow._on_success = fail
    h.flow._save, h.flow._record = h.save, h.record
    h.flow.start()
    view = h.finish()
    assert view == LoginView("failed", "Login failed (TypeError).")
    assert not h.qr_path.exists()
    assert "SYNTH-LP-SECRET" not in capsys.readouterr().err
    h.jobs.clear()
    assert h.flow.start().status == "pending" and len(h.requests) == 2  # not stuck


def test_qr_request_failure_is_failed_and_starts_nothing(tmp_path):
    h = Harness(tmp_path, request_error=requests.ReadTimeout("slow"))
    view = h.flow.start()
    assert view == LoginView("failed", "Login failed: network request failed (ReadTimeout).")
    assert h.jobs == [] and h.opened == [] and not h.qr_path.exists()


def test_a_finished_login_can_start_again(tmp_path):
    h = Harness(tmp_path, poll_error=requests.ReadTimeout("slow"))
    h.flow.start()
    assert h.finish().status == "expired"
    h.jobs.clear()
    assert h.flow.start().status == "pending"
    assert len(h.requests) == 2 and len(h.jobs) == 1


def test_unopenable_qr_only_adds_a_remark(tmp_path):
    h = Harness(tmp_path, opener_result=False)
    view = h.flow.start()
    assert view.status == "pending" and view.message == PENDING + UNOPENED_REMARK
    assert view.png and len(h.jobs) == 1


def test_qr_file_that_cannot_be_written_only_adds_a_remark(tmp_path):
    h = Harness(tmp_path)
    h.qr_path.parent.write_text("not a directory")  # the parent cannot be created
    view = h.flow.start()
    assert view.status == "pending" and view.message.endswith(UNOPENED_REMARK)
    assert h.opened == []
