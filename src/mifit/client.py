"""The ``MiFit`` facade: the one object callers use to read their Mi Fitness data.

It owns the login session and a shared ``Transport`` and delegates each read to
a domain function. Date arguments are either ``"YYYY-MM-DD"`` strings, which
become the bounds of that *local* calendar day, or ints already in Unix seconds.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Container, Iterator, Mapping
from datetime import timedelta, tzinfo
from typing import Any

# ``load_session``, ``save_session``, ``renew_session``, ``record_event`` and
# ``Transport`` are looked up through this module at call time, so tests can patch
# them here and stay offline and off the real session files.
# Importing the domain modules is what registers them in ``REGISTRY``; keep these
# imports even if a name below stops being used.
from mifit import chunks
from mifit.auth.lifetime import record_event
from mifit.auth.qr import login_qr as _login_qr
from mifit.auth.renew import renew_session
from mifit.auth.session import Session, load_session, save_session
from mifit.domains.base import DomainResult, stalled_note
from mifit.domains.consumption import fetch_consumption
from mifit.domains.days import local_midnight, parse_day
from mifit.domains.heart_rate import fetch_heart_rate_full, fetch_heart_rate_window
from mifit.domains.heart_rate_daily import fetch_heart_rate_daily
from mifit.domains.passthrough import fetch_daily_rows, fetch_raw_rows
from mifit.domains.profile import fetch_profile
from mifit.domains.raw_window import RAW_ENDPOINT
from mifit.domains.sleep import fetch_sleep
from mifit.domains.spo2 import fetch_spo2_window
from mifit.domains.spo2_daily import fetch_spo2_daily
from mifit.domains.steps import fetch_steps
from mifit.domains.weight import fetch_weight
from mifit.domains.workout_detail import fetch_workout_detail
from mifit.domains.workouts import fetch_workouts
from mifit.errors import AuthError
from mifit.fds.download import FdsDownloader
from mifit.transport import Transport

_RAW_ENDPOINT = RAW_ENDPOINT

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


def to_epoch_s(
    value: DateArg, *, end_of_day: bool = False, tz: tzinfo | None = None
) -> int:
    """Convert a date argument to Unix seconds.

    An int is taken to be epoch seconds already and returned unchanged. A
    ``"YYYY-MM-DD"`` string is read as a day in ``tz``, the machine's *local*
    timezone by default, and becomes that day's first second (its local midnight)
    or, with ``end_of_day=True``, its last: the second before the next day's local
    midnight. That is ``23:59:59`` on most days; on a day whose clocks go back at
    midnight, ``23:59:59`` happens twice and the second one is the last, so the
    ends of consecutive days always meet with no gap.

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

    day = parse_day(value)
    try:
        if end_of_day:
            return local_midnight(day + timedelta(days=1), tz) - 1
        return local_midnight(day, tz)
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

    ``fds_http`` is used in place of ``requests`` by the object-store downloads of
    ``workout_detail``, for tests (see ``mifit.fds.download.FdsDownloader`` for what
    it must accept). It never sees the session: the download links carry their own
    signature.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        http: Any = None,
        auto_renew: bool = True,
        fds_http: Any = None,
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
        self._fds_http = fds_http
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

    def workout_detail(
        self, start: DateArg, end: DateArg, *, track: bool = True
    ) -> DomainResult:
        """Per-second samples and the GPS track of each workout from ``start`` to ``end``.

        The workouts are the ones ``workouts`` gives for the same arguments, newest
        first, each as a ``WorkoutDetail``: its ``workout`` summary, its ``samples``
        (one ``WorkoutSample`` per recorded second: heart rate, cumulative distance
        and steps, cadence, pace or speed, altitude) and its ``track`` (``TrackPoint``
        fixes). ``raw`` holds ``{"workout", "record", "gps"}`` for each workout, at
        the same index. ``samples`` and ``track`` are ``None`` when the file was not
        read (a note says why) and ``[]`` when it held no points::

            res = client.workout_detail("2026-10-01", "2026-10-14")
            for d in res.records:
                d.workout, d.samples, d.track

            w = client.workouts("2026-10-12", "2026-10-12").records[0]
            one = client.workout_detail(w.start_epoch_s, w.start_epoch_s)  # that one only

            no_gps = client.workout_detail("2026-10-01", "2026-10-14", track=False)

        A track shows where you were. ``track=False`` skips the GPS file: it is not
        requested, ``track`` is ``None`` and no note is added.

        Each workout's files cost up to three requests (a link request and two
        downloads), so one call reads the detail of at most 10 supported workouts,
        the newest; the older ones keep their record, with ``samples`` and ``track``
        ``None``, and a note counts them. Ask for a shorter range, or read week by
        week with ``read_chunks``. Only outdoor runs and rides have a detail so far;
        other workouts get a note and send no request. The files are downloaded
        afresh on every call, outside the session: nothing is cached or written to
        disk.
        """
        return fetch_workout_detail(
            self._transport,
            to_epoch_s(start),
            to_epoch_s(end, end_of_day=True),
            downloader=FdsDownloader(http=self._fds_http),
            track=track,
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
        if you need every row. Use ``raw_window`` for a window that is complete at
        its start.

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

    def raw_window(self, key: str, start: DateArg, end: DateArg) -> DomainResult:
        """The service's rows for any raw fitness-data ``key``, complete at the start.

        ``raw`` holds the rows whose outer ``time`` is in the window, in the service's
        order, with the outer fields intact and ``value`` decoded from its JSON string
        (whatever JSON type it holds; a value that is not JSON stays a string and a
        note counts it). ``records`` is always empty: no key read here becomes a
        record. ``notes`` count the rows skipped for lack of a usable ``time``, the
        values left undecoded, and a read that stalled on an unusable cursor.

        Unlike ``raw`` it asks the service a day early and filters, so the first hours
        of the window are not lost (notes/08, notes/11). Rows of a daily-type key, whose
        outer ``time`` is a UTC midnight, are filtered on that ``time`` as served.
        ``key`` can be any raw key; see ``notes/domain-catalog.md`` for the list and
        what each holds. Raises ``ValueError`` for an empty or non-string ``key``.
        """
        return fetch_raw_rows(
            self._transport, key, to_epoch_s(start), to_epoch_s(end, end_of_day=True)
        )

    def daily_report(
        self, key: str, start: DateArg, end: DateArg, *, tag: str = "daily_report"
    ) -> DomainResult:
        """The service's daily rows for ``key``, one per local day that has data.

        ``raw`` holds every row the service sent for the days from ``start`` to
        ``end`` (inclusive local days, chosen as ``steps`` chooses them), newest
        first, with the outer fields intact (``time`` is the UTC midnight of the
        local date) and ``value`` decoded from its JSON string. ``records`` is always
        empty. ``notes`` count the values left undecoded and a read that stalled on
        an unusable cursor.

        ``tag`` is ``"daily_report"`` for the daily summaries; the ``goal`` key lives
        under ``tag="daily_fitness"``. See ``notes/domain-catalog.md`` for the keys
        and what each holds. Raises ``ValueError`` for an empty or non-string
        ``key`` or ``tag``.
        """
        return fetch_daily_rows(
            self._transport,
            key,
            to_epoch_s(start),
            to_epoch_s(end, end_of_day=True),
            tag=tag,
        )

    def read_chunks(
        self,
        domain: str,
        start: str,
        end: str,
        *,
        size: chunks.ChunkSize | None = None,
        skip: Container[str] = frozenset(),
        options: Mapping[str, Any] | None = None,
    ) -> Iterator[tuple[chunks.Chunk, DomainResult]]:
        """Read ``domain`` from ``start`` to ``end`` in chunks, one at a time.

        ``start`` and ``end`` are ``"YYYY-MM-DD"`` local days, both inclusive, and
        the chunks are blocks of whole local days counted from ``start``: a week
        for raw heart rate, SpO2 and workout detail, a month (31 days) for the
        other domains, unless ``size`` (``"day"``, ``"week"`` or ``"month"``) says
        otherwise. Each chunk is one ordinary call of the domain method, sent
        through this client's transport, so every request keeps the same pacing
        and the whole read shares one renewal budget. Nothing runs in parallel.
        ``options`` are keyword arguments for the domain method, given to every
        chunk's call, such as ``{"track": False}`` for ``workout_detail``.

        Returns a lazy generator of ``(chunk, result)``, newest chunk first
        (oldest first for ``consumption``, as its single read goes); a chunk is
        read only when the next part is asked for. ``skip`` takes the labels
        (``chunk.label``, ``"YYYY-MM-DD..YYYY-MM-DD"``) of chunks already done, to
        resume an interrupted read. An error from a chunk propagates; the parts
        already yielded stay valid. ``mifit.chunks.merge_chunks`` turns the parts
        into one result: its ``records`` equal a single read of the range, and so
        does its ``raw`` except for ``sleep`` (one block of daily values and
        matched sessions per chunk), and for ``workout_detail`` only while each
        call stays within its 10-workout cap; with more than one chunk, each note
        starts with its chunk's label. Unknown domains, dates that are not
        ``"YYYY-MM-DD"`` strings, a reversed range and a truthy ``full`` option
        (a backward read cannot be chunked) are refused before anything is read
        (see ``mifit.chunks.read_chunks``).
        """
        return chunks.read_chunks(
            self, domain, start, end, size=size, skip=skip, options=options
        )

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
