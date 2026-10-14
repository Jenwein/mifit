"""The ``MiFit`` facade: the one object callers use to read their Mi Fitness data.

It owns the login session and a shared ``Transport`` and delegates each read to
a domain function. Date arguments are either ``"YYYY-MM-DD"`` strings, which
become the bounds of that *local* calendar day, or ints already in Unix seconds.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

# ``load_session``, ``save_session``, ``renew_session``, ``record_event`` and
# ``Transport`` are looked up through this module at call time, so tests can patch
# them here and stay offline and off the real session files.
# Importing the domain modules is what registers them in ``REGISTRY``; keep these
# imports even if a name below stops being used.
from mifit.auth.lifetime import record_event
from mifit.auth.qr import login_qr as _login_qr
from mifit.auth.renew import renew_session
from mifit.auth.session import Session, load_session, save_session
from mifit.domains.base import DomainResult, stalled_note
from mifit.domains.consumption import fetch_consumption
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.domains.profile import fetch_profile
from mifit.domains.raw_window import RAW_ENDPOINT
from mifit.domains.sleep import fetch_sleep
from mifit.domains.spo2 import fetch_spo2_window
from mifit.domains.spo2_daily import fetch_spo2_daily
from mifit.domains.steps import fetch_steps
from mifit.domains.weight import fetch_weight
from mifit.domains.workouts import fetch_workouts
from mifit.errors import AuthError
from mifit.transport import Transport

_RAW_ENDPOINT = RAW_ENDPOINT

_DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DAY_START = time(0, 0, 0)
_DAY_END = time(23, 59, 59)

DateArg = str | int
"""A ``"YYYY-MM-DD"`` string (local day) or an int of Unix seconds."""

_RENEWAL_NETWORK_FAILURE = "The session renewal request failed on the network."


def _scrubbed_network_error(exc: OSError) -> OSError:
    """A fresh exception of ``exc``'s type that carries none of its details.

    A ``requests`` error holds the URL it was fetching, in its message, in its
    ``request`` and in the urllib3 error it was raised from; on the renewal's STS
    call that URL carries the one-time ticket and the clientSign. The replacement
    keeps the type, so callers (and the CLI) still see a network failure, with a
    fixed message and nothing else. A type that cannot be built from a message
    alone becomes a ``requests.ConnectionError``.
    """
    try:
        return type(exc)(_RENEWAL_NETWORK_FAILURE)
    except Exception:
        import requests  # imported lazily, as in ``Transport``

        return requests.ConnectionError(_RENEWAL_NETWORK_FAILURE)


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

    With ``auto_renew`` (the default), a request refused with HTTP 401 renews the
    session once from its passToken and is sent again (see ``Transport`` for the
    limits); a renewal that fails raises ``RenewalError``, an ``AuthError``. A
    renewal that fails on the network raises a network error of the same type
    instead, with a fixed message in place of the original's, whose URL carries
    a one-time signature. The renewal runs on the injected ``http`` when one is
    given, so with renewal on that object's ``get`` must also accept the
    ``cookies=`` and ``allow_redirects=`` keywords (a ``requests.Session`` does);
    by default the renewal uses a fresh HTTP client of its own, never the one
    the health requests use. A client whose session came from the store
    (``session`` omitted, or after ``login_qr``) saves the renewed session there
    and logs the 401 and the renewal to the session log; a session passed in is
    renewed in memory only, so read ``session`` to keep it.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        http: Any = None,
        auto_renew: bool = True,
    ) -> None:
        store_backed = session is None
        if session is None:
            unreadable = False
            try:
                session = load_session()
            except ValueError:
                # Raised after the block, so the loader's error is not left on
                # ``__context__``: its message may quote the session file, which
                # holds credentials (``from None`` would only hide it from the
                # printed traceback).
                unreadable = True
            if unreadable:
                raise AuthError(
                    "The stored Mi Fitness session is unreadable. Log in again "
                    "(MiFit.login_qr) to replace it."
                )
        if session is None or not session.is_valid():
            # No detail about the session: it holds credentials.
            raise AuthError(
                "No valid Mi Fitness login session was found. Log in first "
                "(MiFit.login_qr) and the session will be saved for next time."
            )
        self._http = http
        self._auto_renew = auto_renew
        self._store_backed = store_backed
        self._session = session
        self._transport = self._make_transport(session)

    @property
    def session(self) -> Session:
        """The login session in use (read-only; ``login_qr`` and a renewal switch it)."""
        return self._session

    def _make_transport(self, session: Session) -> Transport:
        return Transport(
            session, http=self._http, renew=self._renew if self._auto_renew else None
        )

    def _record(self, event: str, session: Session) -> None:
        """Log ``event`` to the session log, for a store-backed client only."""
        if self._store_backed:
            record_event(event, session)  # best effort: it never raises

    def _renew(self, old: Session) -> Session:
        """The transport's ``renew`` hook: trade ``old``'s passToken for a new session.

        Records ``unauthorized`` and then one of ``renewed``, ``renew_failed`` (the
        account service refused), ``renew_unavailable`` (the session has nothing to
        renew with, so no request was sent) or ``renew_network_error``; a
        store-backed client also saves the new session (a save that fails only
        warns, so the read goes on). Exceptions from the renewal are re-raised,
        except that a network failure is replaced by a fresh exception of the same
        type with a fixed message (see ``_scrubbed_network_error``).
        """
        self._record("unauthorized", old)
        network_error: OSError | None = None
        try:
            # ``self._http`` is None unless one was injected, so by default the
            # renewal builds its own client and its cookies never reach the
            # transport's cookie jar.
            new = renew_session(old, http=self._http)
        except OSError as exc:  # requests' exceptions are OSErrors
            self._record("renew_network_error", old)
            network_error = _scrubbed_network_error(exc)
        except Exception:
            # `renew_session` refuses before any request when the session has no
            # passToken or user id, so there was no verdict from the account service.
            self._record("renew_failed" if old.can_renew() else "renew_unavailable", old)
            raise
        if network_error is not None:
            # Raised here, outside the ``except`` block, so that neither its
            # ``__context__`` nor its ``__cause__`` leads back to the original.
            raise network_error
        self._record("renewed", new)
        self._session = new
        if self._store_backed:
            try:
                save_session(new)
            except OSError as exc:
                warnings.warn(
                    "The renewed session could not be saved "
                    f"({exc.strerror or type(exc).__name__}); the next run will "
                    "renew again.",
                    RuntimeWarning,
                )
        return new

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

        The default is the windowed query. With ``full=True`` the read instead pages
        back from ``end`` without a start (up to the page cap), so ``start`` is
        only validated, not applied.
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

    def workouts(self, start: DateArg, end: DateArg) -> DomainResult:
        """Workouts from ``start`` to ``end`` (inclusive days), newest first.

        One record per workout that started between the start of ``start`` and the
        end of ``end`` (local days). Each record gives the sport, start and end,
        duration, distance, active calories, average and maximum heart rate and
        steps; the full source report for each workout is in ``raw``.
        """
        return fetch_workouts(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def spo2_daily(self, start: DateArg, end: DateArg) -> DomainResult:
        """Daily SpO2 summaries from ``start`` to ``end`` (inclusive days).

        One record per local day with SpO2 data, giving the day's average, highest
        and lowest SpO2 % as the service's daily report states them.
        """
        return fetch_spo2_daily(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def spo2(self, start: DateArg, end: DateArg) -> DomainResult:
        """Raw all-day SpO2 samples from ``start`` to ``end``.

        Every sample in the window, complete at its start: the read asks the
        service a day earlier and filters, because it would otherwise leave out the
        first hours (notes/11). Spot checks (``single_spo2``) and low-SpO2 events
        (``low_spo2``) are separate keys; read them with ``raw``.
        """
        return fetch_spo2_window(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def weight(self, start: DateArg, end: DateArg) -> DomainResult:
        """Every weight record from ``start`` to ``end``, with kg and BMI.

        Body-composition figures stay in ``raw`` (unverified; zero on the account
        checked, notes/11). A long range is fine: records are sparse.
        """
        return fetch_weight(
            self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def profile(self) -> DomainResult:
        """The account profile: height, weight and the daily step and calorie goals.

        One ``UserProfile`` record. ``raw`` holds the whole profile as the service
        sent it, including name, birth date and account ids, so treat it as
        personal data.
        """
        return fetch_profile(self._transport)

    def raw(self, key: str, start: DateArg, end: DateArg) -> list[dict]:
        """The service's own rows for fitness-data ``key``, undecoded and unfiltered.

        The paged read has already unwrapped the response's ``result`` envelope,
        so the entry list is returned exactly as it gave it.

        This is the service's own answer for the window; for bucketed keys such
        as ``heart_rate`` and ``spo2`` (notes/08, notes/11) it omits rows of a
        bucket keyed at or before ``start``, so widen ``start`` and filter yourself
        if you need every row.

        When the service says more rows follow but sends no usable cursor, the
        read stops there: the rows read so far are returned and a
        ``RuntimeWarning`` says some may be missing.
        """
        paged = self._transport.get_paged_result(
            _RAW_ENDPOINT,
            {
                "key": key,
                "start_time": to_epoch_s(start),
                "end_time": to_epoch_s(end, end_of_day=True),
                "reverse": True,
            },
        )
        if paged.stalled:
            warnings.warn(stalled_note(f"'{key}' rows"), RuntimeWarning, stacklevel=2)
        return paged.items

    def login_qr(self, on_qr: Callable[[str, str], None] | None = None) -> None:
        """Log in again by QR code, save the new session and switch to it.

        ``on_qr(login_url, qr_payload)`` is called once the QR code exists so it
        can be shown to the user; the call then blocks until they approve it. If
        the login fails, the current session and transport are left untouched.
        Afterwards the client is store-backed: the login is logged, and later
        renewals are saved.
        """
        session = _login_qr(http=self._http, on_qr=on_qr)
        save_session(session)
        self._session = session
        self._store_backed = True
        self._transport = self._make_transport(session)
        self._record("login", session)
