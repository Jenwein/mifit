"""Login session model and secure local token store.

``Session`` holds the credentials obtained from a Mi account login. Secret
fields are never rendered by ``repr``/``str`` so they cannot leak through logs,
tracebacks or debugger output. Persistence is a single JSON file written with
owner-only permissions where the OS supports POSIX modes.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path

from mifit.paths import default_session_path

_REDACTED = "<redacted>"


@dataclass(repr=False)
class Session:
    """The credentials of one Mi account login.

    ``created_at`` is the **login** time (UTC ISO-8601 ``Z``); an automatic
    renewal keeps it. ``renewed_at`` is the time of the last automatic renewal
    (``None`` = never renewed). ``pass_token`` and ``device_id`` are what a
    renewal needs: the account's passToken and the random device id that was
    sent to the account service at login. Sessions saved before they existed
    have both empty and cannot be renewed.
    """

    user_id: str
    c_user_id: str
    service_token: str
    ssecurity: str
    region: str
    created_at: str
    extra_cookies: dict[str, str] = field(default_factory=dict)
    pass_token: str = ""
    device_id: str = ""
    renewed_at: str | None = None

    def is_valid(self) -> bool:
        """True iff the session carries both a service token and a user id."""
        return bool(self.service_token) and bool(self.user_id)

    def can_renew(self) -> bool:
        """True iff the session carries what an automatic renewal needs."""
        return bool(self.pass_token) and bool(self.user_id)

    def __repr__(self) -> str:
        # service_token / ssecurity / pass_token are secrets and device_id is
        # redacted too; extra_cookies may hold more credentials, so only its
        # cookie names are shown.
        return (
            f"{type(self).__name__}("
            f"user_id={self.user_id!r}, "
            f"c_user_id={self.c_user_id!r}, "
            f"service_token={_REDACTED}, "
            f"ssecurity={_REDACTED}, "
            f"pass_token={_REDACTED}, "
            f"device_id={_REDACTED}, "
            f"region={self.region!r}, "
            f"created_at={self.created_at!r}, "
            f"renewed_at={self.renewed_at!r}, "
            f"extra_cookies=<{len(self.extra_cookies)} cookies: "
            f"{sorted(self.extra_cookies)!r} values redacted>)"
        )


def _to_json(session: Session) -> str:
    data = {f.name: getattr(session, f.name) for f in fields(session)}
    return json.dumps(data, indent=2, sort_keys=True)


def save_session(session: Session, path: Path | None = None) -> Path:
    """Persist ``session`` as JSON and return the path written.

    Parent directories are created (owner-only on POSIX). The file is written to
    a private temp file in the same directory and atomically renamed into place,
    so there is no window in which the secrets are readable by other users and a
    crash cannot leave a truncated session file behind.
    """
    target = Path(path) if path is not None else default_session_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # mkstemp creates the file exclusively and readable/writable by the owner only.
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_to_json(session))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def load_session(path: Path | None = None) -> Session | None:
    """Load a persisted session, or return ``None`` if no file exists.

    Raises ``ValueError`` if the file exists but is not a valid session record.
    Error messages name the problem only; they never include stored values.
    """
    source = Path(path) if path is not None else default_session_path()
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # `from None`: JSONDecodeError carries the whole document in `.doc`.
        raise ValueError(f"session file {source} is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError(f"session file {source} does not contain a JSON object")

    # Only the six fields every session file has ever carried are required; the
    # rest (extra_cookies and the renewal fields) default for older files.
    required = [
        f.name
        for f in fields(Session)
        if f.default is MISSING and f.default_factory is MISSING
    ]
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError(
            f"session file {source} is missing field(s): {', '.join(missing)}"
        )

    return Session(
        **{name: data[name] for name in required},
        extra_cookies=dict(data.get("extra_cookies") or {}),
        pass_token=data.get("pass_token") or "",
        device_id=data.get("device_id") or "",
        renewed_at=data.get("renewed_at") or None,
    )
