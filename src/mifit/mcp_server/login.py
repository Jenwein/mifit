"""The QR login the MCP server runs, for people who never open a terminal.

``LoginFlow.start`` asks the account service for a QR login (``request_qr_login``) and
returns the code at once, as a PNG and its login URL; a background job then waits for
the user to approve it (``poll_qr_login``), trades the result for a session
(``exchange_sts``), saves it where ``mifit login`` does and logs the login. It is
``mifit login``'s flow (``login_qr``: one HTTP client and one device id for the three
requests), split so the tool call does not block for minutes. ``view`` says how it
stands.

The PNG is also written to ``~/.mifit/login-qr.png`` and opened on this computer, for
an AI client that does not show images; the file is deleted when the login ends. A
security confirmation Xiaomi asks for is opened in the local browser: its URL never
leaves this computer, and neither does the long-polling URL, which lives only in the
background job.
"""

from __future__ import annotations

import io
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import qrcode
import requests
from qrcode.image.pure import PyPNGImage

from mifit.auth.lifetime import record_event
from mifit.auth.qr import exchange_sts, new_device_id, poll_qr_login, request_qr_login
from mifit.auth.session import save_session
from mifit.errors import MiFitError, NotificationRequiredError
from mifit.paths import app_data_dir
from mifit.region import SID

__all__ = ["LOGIN_QR_FILE_NAME", "LoginFlow", "LoginView", "qr_png"]

LOGIN_QR_FILE_NAME = "login-qr.png"

_PENDING = (
    "Scan the QR code with the Xiaomi or Mi Fitness app and approve the login, or open "
    "login_url on a phone signed in to the Xiaomi account. Then call login_status."
)
_UNOPENED_REMARK = " (The QR image could not be opened on this computer.)"
_SUCCEEDED = "Logged in. The session is saved on this computer; the data tools work now."
_EXPIRED = (
    "The QR code expired before the login was approved. Call login_start for a new one."
)
_CONFIRM = (
    "Xiaomi asks for a security confirmation. A confirmation page was opened in the "
    "browser on this computer; complete it, then call login_start again."
)
_CONFIRM_UNOPENED = (
    "Xiaomi asks for a security confirmation, but the page could not be opened on this "
    "computer. Run `mifit login` in a terminal instead."
)
_IDLE = "No login in progress. Call login_start to log in."

Status = Literal["idle", "pending", "succeeded", "failed", "confirmation_required", "expired"]


@dataclass(frozen=True)
class LoginView:
    """How the login stands. ``login_url`` and ``png`` (the QR code) are set only
    while it is ``pending``."""

    status: Status
    message: str
    login_url: str | None = None
    png: bytes | None = None


def qr_png(content: str) -> bytes:
    """``content`` as a QR code PNG, drawn as ``mifit login`` draws it in the terminal
    (error correction L, a two-module border)."""
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        border=2,
        image_factory=PyPNGImage,
    )
    code.add_data(content)
    code.make(fit=True)
    out = io.BytesIO()
    code.make_image().save(out)
    return out.getvalue()


def _in_background(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="mifit-login", daemon=True).start()


def _failed(exc: BaseException) -> LoginView:
    """The view of a login that failed with ``exc``: the SDK's own message (written
    without secrets), or the exception's type for anything else, whose text may hold
    a URL."""
    if isinstance(exc, requests.RequestException):
        return LoginView("failed", f"Login failed: network request failed ({type(exc).__name__}).")
    if isinstance(exc, MiFitError):
        return LoginView("failed", f"Login failed: {exc}")
    return LoginView("failed", f"Login failed ({type(exc).__name__}).")


class LoginFlow:
    """One QR login at a time. ``on_success`` runs after the new session is saved (the
    server drops its client then, so the next read opens the new session). The other
    arguments replace the account requests, the session store, the log, the local
    opener and the background thread, for tests."""

    def __init__(
        self,
        *,
        on_success: Callable[[], None],
        qr_path: Path | None = None,
        opener: Callable[[str], Any] = webbrowser.open,
        spawn: Callable[[Callable[[], None]], None] = _in_background,
        request: Callable[..., Any] = request_qr_login,
        poll: Callable[..., Any] = poll_qr_login,
        exchange: Callable[..., Any] = exchange_sts,
        save: Callable[..., Any] = save_session,
        record: Callable[..., Any] = record_event,
        http_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._on_success = on_success
        self._qr_path = qr_path if qr_path is not None else app_data_dir() / LOGIN_QR_FILE_NAME
        self._opener = opener
        self._spawn = spawn
        self._request = request
        self._poll = poll
        self._exchange = exchange
        self._save = save
        self._record = record
        self._http_factory = http_factory if http_factory is not None else requests.Session
        self._start_lock = threading.Lock()  # one start at a time
        self._view_lock = threading.Lock()  # the view, also set by the job's thread
        self._view = LoginView("idle", _IDLE)

    def view(self) -> LoginView:
        with self._view_lock:
            return self._view

    def _set(self, view: LoginView) -> None:
        with self._view_lock:
            self._view = view

    def start(self) -> LoginView:
        """Start a login and return its QR code, or the running login's.

        Blocks for the QR request only; waiting for the approval runs in the
        background (``spawn``).
        """
        with self._start_lock:
            current = self.view()
            if current.status == "pending":
                return current
            http = self._http_factory()
            device_id = new_device_id()
            try:
                challenge = self._request(http, SID, device_id=device_id)
            except Exception as exc:
                failed = _failed(exc)
            else:
                failed = None
            if failed is not None:
                self._set(failed)
                return failed

            png = qr_png(challenge.login_url)
            message = _PENDING if self._show(png) else _PENDING + _UNOPENED_REMARK
            self._set(LoginView("pending", message, challenge.login_url, png))

            def job() -> None:
                self._finish(challenge, http, device_id)

            self._spawn(job)
            return self.view()

    def _show(self, png: bytes) -> bool:
        """Write the QR code to its file and open it here; whether that worked."""
        try:
            self._qr_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._qr_path.write_bytes(png)
            return bool(self._opener(self._qr_path.as_uri()))
        except Exception:  # best effort: the code is in the tool result as well
            return False

    def _open(self, url: str) -> bool:
        try:
            return bool(self._opener(url))
        except Exception:
            return False

    def _finish(self, challenge: Any, http: Any, device_id: str) -> None:
        """The background job: wait for the approval and save the session. It always
        ends with a final view, whatever goes wrong, so a login is never stuck
        ``pending`` and nothing reaches the thread's stderr."""
        failure = None
        try:
            final = self._approve(challenge, http, device_id)
        except Exception as exc:
            failure = exc
        if failure is not None:
            final = _failed(failure)
        try:
            self._qr_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._set(final)

    def _approve(self, challenge: Any, http: Any, device_id: str) -> LoginView:
        try:
            result = self._poll(challenge, http, device_id=device_id)
            session = self._exchange(result, SID, http, device_id=device_id)
        except NotificationRequiredError as exc:
            url = exc.notification_url
            opened = self._open(url)
            return LoginView("confirmation_required", _CONFIRM if opened else _CONFIRM_UNOPENED)
        except requests.ReadTimeout:  # the long poll ran out: nobody approved in time
            return LoginView("expired", _EXPIRED)
        except Exception as exc:
            return _failed(exc)
        try:
            self._save(session)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            return LoginView("failed", f"Login failed: could not save the session ({reason}).")
        self._record("login", session)
        self._on_success()
        return LoginView("succeeded", _SUCCEEDED)
