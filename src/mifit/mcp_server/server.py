"""The MCP server: the tool table (``tools.py``) as tools on an ``MCPServer``.

Every tool answers with one text block of compact JSON (``present.encode``) and
declares no output schema (``structured_output=False``): a structured copy would
reach the AI client's context twice. Each tool body runs through ``guarded``, so
a failure reaches the client as a ``ToolError`` with a safe one-line message; the
SDK would otherwise log an unexpected exception with its full text and traceback,
and the text of a network error holds the URL it was fetching.

Reads go through one ``Access`` (one client, one lock), each on a worker thread so
the event loop keeps answering while a read waits on its pacing; a long range is
read chunk by chunk (``mifit.chunks``), with a progress notification after each
chunk when the client asked for them. Successful results are cached for five
minutes (``TtlCache``); ``get_status`` is not cached and never opens a client.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from datetime import date, datetime, tzinfo
from typing import Annotated, Any, TypeVar

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context, Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import mifit
from mifit.auth import session as session_store
from mifit.auth.lifetime import format_age, session_ages
from mifit.chunks import merge_chunks, plan_chunks
from mifit.domains.base import DomainResult
from mifit.mcp_server import tools
from mifit.mcp_server.access import Access
from mifit.mcp_server.cache import TtlCache
from mifit.mcp_server.errors import (
    GPS_DISABLED,
    LOGIN_ADVICE,
    NOT_LOGGED_IN,
    ToolFailure,
)
from mifit.mcp_server.login import LoginFlow
from mifit.mcp_server.present import (
    encode,
    iso_time,
    local_offset_s,
    offset_label,
    present_record,
    range_header,
    without_stages,
)
from mifit.mcp_server.resample import samples_block, series_block, track_block

__all__ = [
    "INSTRUCTIONS",
    "LOGIN_ANNOTATIONS",
    "PRIVACY_NOTE",
    "READ_ANNOTATIONS",
    "SERVER_NAME",
    "build_server",
    "guarded",
]

SERVER_NAME = "mifit"

INSTRUCTIONS = """\
mifit reads the user's own Mi Fitness (Xiaomi) health data from the Xiaomi cloud. All tools are read-only.
Call get_status first: it gives today's local date, the time zone and whether the user is logged in.
If the user is not logged in, call login_start and show them the QR code it returns.
Dates are local calendar days in YYYY-MM-DD form; work out "yesterday" or "last week" from get_status.
Start with the daily tools (get_steps, get_sleep, get_heart_rate_daily, ...); use get_heart_rate, get_spo2 and get_workout_detail only when the detail is needed.
Limits per call: 92 days for daily tools (31 for get_calories), 7 days for get_heart_rate, get_spo2 and sleep stages; dense results are cut down to at most 500 rows and the notes say how.
The figures were checked against the Mi Fitness app. Tell the user what the notes say. This is not medical advice."""

PRIVACY_NOTE = (
    "Tool results are sent to the AI assistant's model provider. The Mi Fitness login "
    "stays on this computer; GPS tracks are only available when the server was started "
    "with --allow-gps (in the Claude Desktop extension: when its Allow GPS tracks switch "
    "is on)."
)

READ_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True, open_world_hint=True, idempotent_hint=True
)

# The login tools write the session file (``~/.mifit``), so they are not read-only;
# they only ever add a login, never remove anything.
LOGIN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, open_world_hint=True
)

logger = logging.getLogger("mifit.mcp_server")

T = TypeVar("T")

# The tools' parameters, with the descriptions the AI client reads.
StartDate = Annotated[str, Field(description=tools.START_DATE_DOC)]
EndDate = Annotated[str | None, Field(description=tools.END_DATE_DOC)]
IncludeStages = Annotated[bool, Field(description=tools.INCLUDE_STAGES_DOC)]
IntervalMin = Annotated[
    int | None, Field(ge=0, le=1440, description=tools.INTERVAL_MIN_DOC)
]
WorkoutStart = Annotated[str | int, Field(description=tools.WORKOUT_START_DOC)]
IntervalS = Annotated[int | None, Field(ge=1, le=3600, description=tools.INTERVAL_S_DOC)]
IncludeTrack = Annotated[bool, Field(description=tools.INCLUDE_TRACK_DOC)]

# The value column and the name of each dense series.
_SERIES = {"heart_rate": ("bpm", "heart rate"), "spo2": ("spo2_pct", "SpO2")}


async def guarded(name: str, body: Callable[[], Awaitable[T]]) -> T:
    """``await body()``, with what it raises turned into a ``ToolError`` the client
    may see.

    A ``ToolFailure`` keeps its (safe) message. Anything else is named by its type
    only, here and in the one log line, because its text may hold a credential.
    """
    try:
        return await body()
    except ToolFailure as failure:
        message = str(failure)
    except Exception as exc:
        kind = type(exc).__name__
        logger.error("%s failed (%s)", name, kind)
        message = f"Internal error in mifit ({kind})."
    # Raised here, after the handlers have ended, so the error leads back to no
    # original (a context manager could not do this: its exit runs while the
    # original is still being handled).
    raise ToolError(message)


def _call(method: str, start: Any, end: Any, options: dict, client: Any) -> DomainResult:
    return getattr(client, method)(start, end, **options)


def _profile(client: Any) -> DomainResult:
    return client.profile()


class _Tools:
    """The state the tools share and the reads and answers behind them."""

    def __init__(
        self,
        access: Access,
        cache: TtlCache,
        *,
        allow_gps: bool,
        tz: tzinfo | None,
        now: Callable[[], datetime],
        session_loader: Callable[[], Any],
        login: Any,
    ) -> None:
        self.access = access
        self.cache = cache
        self.allow_gps = allow_gps
        self.tz = tz
        self.now = now
        self.session_loader = session_loader
        self.login = login

    async def cached(self, key: Hashable, make: Callable[[], Awaitable[str]]) -> str:
        """The cached answer for ``key``, or ``make()``'s, stored if it succeeds."""
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        text = await make()
        self.cache.put(key, text)
        return text

    async def read(self, fn: Callable[[Any], DomainResult]) -> DomainResult:
        """``fn(client)`` through ``Access``, on a worker thread."""
        return await anyio.to_thread.run_sync(self.access.read, fn)

    async def read_range(
        self, method: str, first: date, last: date, options: dict, ctx: Context
    ) -> DomainResult:
        """``method`` over the local days ``first`` to ``last``, chunk by chunk as
        ``mifit.chunks`` plans it, merged; progress after each chunk of several."""
        plan = plan_chunks(method, first, last)
        parts = []
        for index, chunk in enumerate(plan, 1):
            call = functools.partial(
                _call, method, chunk.first.isoformat(), chunk.last.isoformat(), options
            )
            parts.append((chunk, await self.read(call)))
            if len(plan) > 1:
                await ctx.report_progress(index, len(plan), f"read {chunk.label}")
        return merge_chunks(parts)

    def records(self, result: DomainResult) -> list[dict]:
        return [present_record(record, tz=self.tz) for record in result.records]

    async def daily_text(
        self, tool: tools.DataTool, first: date, last: date, ctx: Context
    ) -> str:
        result = await self.read_range(tool.method, first, last, {}, ctx)
        return encode(
            {
                **range_header(tool.name, first, last, tz=self.tz),
                "records": self.records(result),
                "notes": list(result.notes),
            }
        )

    async def sleep_text(
        self, tool: tools.DataTool, first: date, last: date, stages: bool, ctx: Context
    ) -> str:
        result = await self.read_range(tool.method, first, last, {}, ctx)
        records = self.records(result)
        if not stages:
            records = [without_stages(record) for record in records]
        return encode(
            {
                **range_header(tool.name, first, last, tz=self.tz),
                "records": records,
                "notes": list(result.notes),
            }
        )

    async def series_text(
        self,
        tool: tools.DataTool,
        first: date,
        last: date,
        interval_min: int | None,
        ctx: Context,
    ) -> str:
        result = await self.read_range(tool.method, first, last, {}, ctx)
        value, what = _SERIES[tool.method]
        points = [(record.epoch_s, getattr(record, value)) for record in result.records]
        block, notes = series_block(
            points, value=value, what=what, interval_min=interval_min, tz=self.tz
        )
        return encode(
            {
                **range_header(tool.name, first, last, tz=self.tz),
                **block,
                "notes": list(result.notes) + notes,
            }
        )

    async def detail_text(
        self, tool: tools.DataTool, start: int, interval_s: int | None, track: bool
    ) -> str:
        result = await self.read(
            functools.partial(_call, tool.method, start, start, {"track": track})
        )
        notes = list(result.notes)
        if not result.records:
            moment = iso_time(start, tz=self.tz)
            notes.append(f"No workout starts at {moment}; get_workouts gives the start times.")
            return encode(
                {
                    "tool": tool.name,
                    "workout_start": moment,
                    "timezone": offset_label(local_offset_s(start, self.tz)),
                    "workout": None,
                    "samples": None,
                    "track": None,
                    "notes": notes,
                }
            )
        detail = result.records[0]
        offset = detail.workout.utc_offset_s
        samples = None
        if detail.samples is not None:
            samples, extra = samples_block(
                detail.samples, interval_s=interval_s, offset_s=offset, tz=self.tz
            )
            notes += extra
        route = None
        if track and detail.track is not None:  # never a track the gate did not let through
            route, extra = track_block(detail.track, offset_s=offset, tz=self.tz)
            notes += extra
        zone = offset if offset is not None else local_offset_s(start, self.tz)
        return encode(
            {
                "tool": tool.name,
                "workout_start": iso_time(start, offset_s=offset, tz=self.tz),
                "timezone": offset_label(zone),
                "workout": present_record(detail.workout, tz=self.tz),
                "samples": samples,
                "track": route,
                "notes": notes,
            }
        )

    async def profile_text(self) -> str:
        result = await self.read(_profile)
        return encode(
            {"tool": "get_profile", "records": self.records(result), "notes": list(result.notes)}
        )

    def status(self) -> dict[str, Any]:
        """Today, the zone, the login and the GPS gate; reads the stored session only."""
        moment = self.now()
        offset = moment.utcoffset()
        status: dict[str, Any] = {
            "tool": "get_status",
            "version": mifit.__version__,
            "today": moment.date().isoformat(),
            "timezone": offset_label(int(offset.total_seconds()) if offset else 0),
        }
        try:
            stored = self.session_loader()
            problem = NOT_LOGGED_IN
        except ValueError:  # the file exists but is not a session
            stored = None
            problem = "The stored Mi Fitness session is unreadable. " + LOGIN_ADVICE
        if stored is not None and stored.is_valid():
            login_age_s, token_age_s = session_ages(stored, moment)
            status["logged_in"] = True
            status["session"] = {
                "login_age": format_age(login_age_s),
                "token_age": format_age(token_age_s),
                "auto_renew": stored.can_renew(),
            }
        else:
            status["logged_in"] = False
            status["session"] = None
        status["login"] = self.login.view().status
        if not status["logged_in"]:
            status["next_step"] = problem
        status["gps_enabled"] = self.allow_gps
        status["privacy"] = PRIVACY_NOTE
        return status


def _daily_tool(state: _Tools, tool: tools.DataTool) -> Callable[..., Awaitable[str]]:
    async def daily(start_date: StartDate, ctx: Context, end_date: EndDate = None) -> str:
        async def body() -> str:
            first, last = tools.parse_range(
                start_date, end_date, max_days=tool.max_days, tool=tool.name
            )
            return await state.cached(
                (tool.name, first, last), lambda: state.daily_text(tool, first, last, ctx)
            )

        return await guarded(tool.name, body)

    return daily


def _sleep_tool(state: _Tools, tool: tools.DataTool) -> Callable[..., Awaitable[str]]:
    async def sleep(
        start_date: StartDate,
        ctx: Context,
        end_date: EndDate = None,
        include_stages: IncludeStages = False,
    ) -> str:
        async def body() -> str:
            if include_stages:
                max_days = tools.SLEEP_STAGES_MAX_DAYS
                label = f"{tool.name} with include_stages=true"
            else:
                max_days, label = tool.max_days, tool.name
            first, last = tools.parse_range(start_date, end_date, max_days=max_days, tool=label)
            return await state.cached(
                (tool.name, first, last, include_stages),
                lambda: state.sleep_text(tool, first, last, include_stages, ctx),
            )

        return await guarded(tool.name, body)

    return sleep


def _series_tool(state: _Tools, tool: tools.DataTool) -> Callable[..., Awaitable[str]]:
    async def series(
        start_date: StartDate,
        ctx: Context,
        end_date: EndDate = None,
        interval_min: IntervalMin = None,
    ) -> str:
        async def body() -> str:
            first, last = tools.parse_range(
                start_date, end_date, max_days=tool.max_days, tool=tool.name
            )
            return await state.cached(
                (tool.name, first, last, interval_min),
                lambda: state.series_text(tool, first, last, interval_min, ctx),
            )

        return await guarded(tool.name, body)

    return series


def _detail_tool(state: _Tools, tool: tools.DataTool) -> Callable[..., Awaitable[str]]:
    async def detail(
        workout_start: WorkoutStart,
        interval_s: IntervalS = None,
        include_track: IncludeTrack = False,
    ) -> str:
        async def body() -> str:
            start = tools.parse_workout_start(workout_start)
            if include_track and not state.allow_gps:
                raise ToolFailure(GPS_DISABLED)
            track = state.allow_gps and include_track
            return await state.cached(
                (tool.name, start, interval_s, track),
                lambda: state.detail_text(tool, start, interval_s, track),
            )

        return await guarded(tool.name, body)

    return detail


_FACTORIES = {
    "daily": _daily_tool,
    "sleep": _sleep_tool,
    "series": _series_tool,
    "detail": _detail_tool,
}


def build_server(
    client_factory: Callable[[], Any] | None = None,
    *,
    allow_gps: bool = False,
    tz: tzinfo | None = None,
    now: Callable[[], datetime] | None = None,
    clock: Callable[[], float] = time.monotonic,
    session_loader: Callable[[], Any] | None = None,
    login_factory: Callable[[Callable[[], None]], Any] | None = None,
) -> MCPServer:
    """The MCP server with every tool registered.

    ``client_factory`` opens the client (``mifit.MiFit``, from the stored session, by
    default); ``allow_gps`` is the server's GPS gate (``--allow-gps``, or the Claude
    Desktop extension's "Allow GPS tracks" switch); ``tz`` the zone
    times are shown in (the machine's by default); ``now`` gives the aware current
    time for ``get_status``; ``clock`` drives the cache; ``session_loader`` reads the
    stored session for ``get_status`` (``mifit.auth.session.load_session``).
    ``login_factory(on_success)`` makes the QR login of ``login_start`` (a
    ``LoginFlow`` by default); ``on_success`` drops the server's client, so the next
    read opens the newly saved session, and clears the cached results.
    """

    def default_factory() -> Any:
        return mifit.MiFit()

    def default_loader() -> Any:
        return session_store.load_session()

    def default_now() -> datetime:
        return datetime.now(tz) if tz is not None else datetime.now().astimezone()

    def default_login(on_success: Callable[[], None]) -> LoginFlow:
        return LoginFlow(on_success=on_success)

    access = Access(client_factory or default_factory)
    cache = TtlCache(clock=clock)

    def logged_in() -> None:
        # The new session may be another account's: open it, and forget the old
        # account's results.
        access.reset()
        cache.clear()

    login = (login_factory or default_login)(logged_in)
    state = _Tools(
        access,
        cache,
        allow_gps=allow_gps,
        tz=tz,
        now=now or default_now,
        session_loader=session_loader or default_loader,
        login=login,
    )
    server = MCPServer(SERVER_NAME, version=mifit.__version__, instructions=INSTRUCTIONS)

    async def status_text() -> str:
        return encode(state.status())

    async def get_status() -> str:
        return await guarded("get_status", status_text)

    async def get_profile() -> str:
        return await guarded(
            "get_profile", lambda: state.cached(("get_profile",), state.profile_text)
        )

    async def start_login() -> Any:
        view = await anyio.to_thread.run_sync(login.start)
        text = encode(
            {
                "tool": "login_start",
                "status": view.status,
                "message": view.message,
                "login_url": view.login_url,
            }
        )
        if view.png:
            return [text, Image(data=view.png, format="png")]
        return text

    async def login_start() -> Any:
        return await guarded("login_start", start_login)

    async def login_view_text() -> str:
        view = login.view()
        return encode({"tool": "login_status", "status": view.status, "message": view.message})

    async def login_status() -> str:
        return await guarded("login_status", login_view_text)

    def add(
        fn: Callable[..., Any],
        name: str,
        title: str,
        description: str,
        annotations: ToolAnnotations = READ_ANNOTATIONS,
    ) -> None:
        server.add_tool(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            structured_output=False,
        )

    add(get_status, "get_status", "Status", tools.STATUS_DESCRIPTION)
    for tool in tools.DATA_TOOLS:
        add(_FACTORIES[tool.kind](state, tool), tool.name, tool.title, tool.description)
    add(get_profile, "get_profile", "Profile", tools.PROFILE_DESCRIPTION)
    add(
        login_start, "login_start", "Log in by QR code", tools.LOGIN_START_DESCRIPTION,
        LOGIN_ANNOTATIONS,
    )
    add(
        login_status, "login_status", "Login status", tools.LOGIN_STATUS_DESCRIPTION,
        LOGIN_ANNOTATIONS,
    )
    return server
