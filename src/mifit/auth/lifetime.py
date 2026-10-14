"""Observe how long a session lives: its ages, a one-line description and an event log.

The service token's real lifetime is not documented anywhere, so the SDK records
when each session event happens and how old the session and its token were at
that moment. The events are ``login``; ``unauthorized`` (a request got HTTP 401);
and then how the renewal that follows ended: ``renewed``, ``renew_failed`` (the
account service refused the stored passToken, or the exchange failed),
``renew_unavailable`` (the session has no passToken or user id, so nothing was
sent) or ``renew_network_error`` (the renewal did not complete because of a network
failure, which can come after the passToken was accepted, so it says nothing about
the passToken). Only ``renew_failed`` can bear on the passToken's lifetime, and only
as an upper bound on refusals: a failed exchange after an accepted passToken is
logged as ``renew_failed`` too. A 401 can also come from a refused request
signature, so an ``unauthorized`` is not always an expiry. The log holds only
event names, UTC times and ages in seconds: never a token, a cookie, a user id or
a device id.
Writing it is best effort and never raises, so a read-only disk can not turn a
working request into a failure.

Times are the UTC ISO-8601 strings stored on ``Session`` (``2026-10-14T03:18:19Z``).
A time that does not parse gives an age of ``None`` ("unknown"); a time in the
future (a clock set back) counts as age 0.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mifit.auth.session import Session
from mifit.paths import default_session_log_path

__all__ = ["describe_session", "format_age", "record_event", "session_ages"]


def _as_utc(moment: datetime) -> datetime:
    """``moment`` in UTC; a naive datetime is taken to be UTC already."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    """Parse a stored ISO-8601 time to an aware UTC datetime, or ``None`` if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except (ValueError, OverflowError):
        return None


def _age_s(then: str | None, now: datetime) -> int | None:
    moment = _parse_time(then)
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds()))


def _now(now: datetime | None) -> datetime:
    return datetime.now(timezone.utc) if now is None else _as_utc(now)


def session_ages(
    session: Session, now: datetime | None = None
) -> tuple[int | None, int | None]:
    """Return ``(login_age_s, token_age_s)``: seconds since login and since the token was issued.

    The token age counts from ``renewed_at``, or from ``created_at`` when the
    session was never renewed. An unparsable time gives ``None`` for that age and
    a time in the future gives 0. ``now`` defaults to the current time.
    """
    moment = _now(now)
    token_since = session.renewed_at or session.created_at
    return _age_s(session.created_at, moment), _age_s(token_since, moment)


def format_age(seconds: int | None) -> str:
    """Render an age for people: ``unknown``, ``45m``, ``7h02m`` or ``2d03h``."""
    if seconds is None:
        return "unknown"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def describe_session(session: Session, now: datetime | None = None) -> str:
    """One line on the session's age and whether it can renew itself; no credential in it."""
    login_age_s, token_age_s = session_ages(session, now)
    text = f"logged in {session.created_at} ({format_age(login_age_s)} ago); "
    if session.renewed_at:
        text += f"last renewed {session.renewed_at} ({format_age(token_age_s)} ago)"
    else:
        text += "never renewed"
    if session.can_renew():
        return text + "; automatic renewal available"
    return (
        text
        + "; automatic renewal unavailable (no passToken: run `mifit login` once to enable it)"
    )


def record_event(
    event: str,
    session: Session,
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> None:
    """Append one line about ``event`` to the session log; never raise.

    The line is ``{"event", "at", "login_age_s", "token_age_s"}`` with ``at`` in
    UTC. ``path`` defaults to ``paths.default_session_log_path()``. The parent
    directory is created owner-only (on POSIX). Any ``OSError`` is swallowed, and
    so is the ``RuntimeError`` that ``Path.home()`` raises when there is no home
    directory to put the default log in.
    """
    moment = _now(now)
    login_age_s, token_age_s = session_ages(session, moment)
    line = json.dumps(
        {
            "event": event,
            "at": moment.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "login_age_s": login_age_s,
            "token_age_s": token_age_s,
        }
    )
    try:
        target = Path(path) if path is not None else default_session_log_path()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except (OSError, RuntimeError):
        pass
