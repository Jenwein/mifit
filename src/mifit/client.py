"""The ``MiFit`` facade: the one object callers use to read their Mi Fitness data.

It owns the login session and a shared ``Transport`` and delegates each read to
a domain function. Date arguments are either ``"YYYY-MM-DD"`` strings, which
become the bounds of that *local* calendar day, or ints already in Unix seconds.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

# Importing the domain modules is what registers them in ``REGISTRY``; keep these
# imports even if a name below stops being used.
from mifit.auth.qr import login_qr as _login_qr
from mifit.auth.session import Session, load_session, save_session
from mifit.domains.base import DomainResult
from mifit.domains.consumption import fetch_consumption
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.domains.sleep import fetch_sleep
from mifit.domains.steps import fetch_steps
from mifit.errors import AuthError
from mifit.transport import Transport

_RAW_ENDPOINT = "data/get_fitness_data_by_time"

_DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DAY_START = time(0, 0, 0)
_DAY_END = time(23, 59, 59)

DateArg = str | int
"""A ``"YYYY-MM-DD"`` string (local day) or an int of Unix seconds."""


def to_epoch_s(value: DateArg, *, end_of_day: bool = False) -> int:
    """Convert a date argument to Unix seconds.

    An int is taken to be epoch seconds already and returned unchanged. A
    ``"YYYY-MM-DD"`` string is read as a day in the machine's *local* timezone and
    becomes that day's first second (``00:00:00``) or, with ``end_of_day=True``,
    its last (``23:59:59``).

    Raises ``TypeError`` for anything that is neither a string nor an int (a
    ``bool`` is rejected, not treated as 0/1) and ``ValueError`` for a string that
    is not a real ``YYYY-MM-DD`` date.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(
            "date must be a 'YYYY-MM-DD' string or an int of epoch seconds, "
            f"not {type(value).__name__}"
        )
    if isinstance(value, int):
        return value

    if not _DATE_FORMAT.fullmatch(value):
        raise ValueError(f"date must look like 'YYYY-MM-DD', got {value!r}")
    try:
        day = date.fromisoformat(value)
        # A naive datetime is interpreted in local time by ``timestamp()``.
        moment = datetime.combine(day, _DAY_END if end_of_day else _DAY_START)
        return int(moment.timestamp())
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"{value!r} is not a supported calendar date") from None


class MiFit:
    """Read access to a Mi Fitness account.

    ``session`` defaults to the one stored by an earlier login. ``http`` is any
    object ``Transport`` accepts (a ``requests.Session`` by default); inject one
    to keep a client offline. Raises ``AuthError`` when there is no usable session.
    """

    def __init__(self, session: Session | None = None, *, http: Any = None) -> None:
        if session is None:
            try:
                session = load_session()
            except ValueError:
                # `from None`: the loader's message may quote file contents, and
                # the file holds credentials.
                raise AuthError(
                    "The stored Mi Fitness session is unreadable. Log in again "
                    "(MiFit.login_qr) to replace it."
                ) from None
        if session is None or not session.is_valid():
            # No detail about the session: it holds credentials.
            raise AuthError(
                "No valid Mi Fitness login session was found. Log in first "
                "(MiFit.login_qr) and the session will be saved for next time."
            )
        self._http = http
        self._session = session
        self._transport = Transport(session, http=http)

    def steps(self, start: DateArg, end: DateArg) -> DomainResult:
        """Daily step summaries from ``start`` to ``end`` (inclusive days)."""
        return fetch_steps(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def consumption(self, start: DateArg, end: DateArg) -> DomainResult:
        """Daily calorie consumption from ``start`` to ``end`` (inclusive).

        One record per local day that has data. ``resting_kcal`` is resting metabolism
        (BMR, from the profile), ``active_kcal`` is the activity calories (this equals
        the app's activity-calorie figure), ``intake_kcal`` is food, and ``total_kcal``
        is resting plus active. The app's calorie ring is composited on the device
        (cloud activity plus phone-side activity), so the most recent day can read
        lower than the app.

        Days are the machine's local calendar days, each requested by its local
        midnight as the app does, so each record's ``date`` is the local date it
        covers.
        """
        return fetch_consumption(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def sleep(self, start: DateArg, end: DateArg) -> DomainResult:
        """Sleep per local day from ``start`` to ``end`` (inclusive).

        One record per day that has sleep: its totals and each session that ended on
        that day (a night, a nap), with the stage timeline where the service has one.
        Session times are true Unix seconds; ``utc_offset_s`` gives the zone they
        were recorded in.
        """
        return fetch_sleep(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def heart_rate(
        self, start: DateArg, end: DateArg, *, full: bool = False
    ) -> DomainResult:
        """Raw heart-rate samples from ``start`` to ``end``.

        The default is the windowed query, whose result is flagged as possibly
        incomplete. With ``full=True`` the fuller read is used instead: it pages
        backwards from ``end`` and sends no start time, so ``start`` is only
        validated, not applied.
        """
        start_s = to_epoch_s(start)
        end_s = to_epoch_s(end, end_of_day=True)
        if full:
            return fetch_heart_rate_full(self._transport, end_s)
        return fetch_heart_rate_window(self._transport, start_s, end_s)

    def heart_rate_daily(self, start: DateArg, end: DateArg) -> DomainResult:
        """Daily heart-rate summaries from ``start`` to ``end`` (inclusive days).

        One record per local day with data, giving the day's average, highest, lowest
        and resting heart rate as the app shows them.
        """
        return fetch_heart_rate_daily(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def raw(self, key: str, start: DateArg, end: DateArg) -> list[dict]:
        """The service's own rows for fitness-data ``key``, undecoded and unfiltered.

        ``get_paged`` has already unwrapped the response's ``result`` envelope, so
        the entry list is returned exactly as it gave it.
        """
        return self._transport.get_paged(
            _RAW_ENDPOINT,
            {
                "key": key,
                "start_time": to_epoch_s(start),
                "end_time": to_epoch_s(end, end_of_day=True),
                "reverse": True,
            },
        )

    def login_qr(self, on_qr: Callable[[str, str], None] | None = None) -> None:
        """Log in again by QR code, save the new session and switch to it.

        ``on_qr(login_url, qr_payload)`` is called once the QR code exists so it
        can be shown to the user; the call then blocks until they approve it. If
        the login fails, the current session and transport are left untouched.
        """
        session = _login_qr(http=self._http, on_qr=on_qr)
        save_session(session)
        self._session = session
        self._transport = Transport(session, http=self._http)
