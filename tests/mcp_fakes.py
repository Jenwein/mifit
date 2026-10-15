"""Fakes and helpers for the MCP server tests (imported as ``from mcp_fakes import
...``; pytest puts ``tests/`` on ``sys.path``). Every credential and figure here is
synthetic.

The helpers run an event loop: a test that uses them needs the ``allow_loopback``
fixture (``tests/conftest.py``).
"""

import json
import threading
import time

import anyio
from mcp import Client

from mifit.auth.session import Session
from mifit.domains.base import DomainResult
from mifit.mcp_server import tools
from mifit.models.activity import ActivityDailySummary
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

SERVICE_TOKEN = "SYNTH-MCP-SERVICE-TOKEN"
SSECURITY = "SYNTH-MCP-SSECURITY"
PASS_TOKEN = "SYNTH-MCP-PASSTOKEN"
DEVICE_ID = "SYNTH-MCP-DEVICE-ID"
COOKIE = "SYNTH-MCP-EXTRA-COOKIE"
SECRETS = (SERVICE_TOKEN, SSECURITY, PASS_TOKEN, DEVICE_ID, COOKIE)
"""Every credential of the fake session: none may appear in any output or log."""

RAW_MARKER = "SYNTH-RAW-MARKER"
"""In every canned ``raw``: the server must never give ``raw``."""

P1_TOOLS = frozenset({"get_status", "get_profile", *(t.name for t in tools.DATA_TOOLS)})

DAY = 1790784000  # 2026-10-01 00:00 +08:00
WORKOUT = Workout(
    start_epoch_s=1790820000,  # 2026-10-01 10:00 +08:00
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
PROFILE = UserProfile(
    height_cm=175.0, weight_kg=70.0, step_goal=8000, calorie_goal_kcal=None, activity_goal_min=30
)


def make_session(created_at="2026-10-14T01:30:00Z"):
    return Session(
        user_id="1000001",
        c_user_id="synthetic-mcp-cuser",
        service_token=SERVICE_TOKEN,
        ssecurity=SSECURITY,
        region="cn",
        created_at=created_at,
        extra_cookies={"synthetic": COOKIE},
        pass_token=PASS_TOKEN,
        device_id=DEVICE_ID,
    )


def _records(method, options):
    """Fresh canned records of ``method`` (real models, synthetic figures)."""
    if method == "steps":
        return [StepRecord("2026-10-01", 4321, 3100, 150, DAY)]
    if method == "consumption":
        return [ConsumptionRecord("2026-10-01", DAY, 1500.5, 250.0, 0.0, 1750.5)]
    if method == "activity_daily":
        return [ActivityDailySummary("2026-10-01", DAY, 0, 45, None)]
    if method == "heart_rate_daily":
        return [HeartRateDailySummary("2026-10-01", DAY, 70, 130, 50, None)]
    if method == "sleep":
        session = SleepSession(
            1790812800, 1790820000, 28800, 120, 60, 60, None, 0,
            [SleepStage(1790812800, 1790816400, "deep"), SleepStage(1790816400, 1790820000, "light")],
        )
        return [
            SleepRecord(
                "2026-10-01", DAY, 120, 60, 60, None, 0, 60, 55, 70, None, None, None, [session]
            )
        ]
    if method == "spo2_daily":
        return [Spo2DailySummary("2026-10-01", DAY, 96, 99, None)]
    if method == "stress_daily":
        return [StressDailySummary("2026-10-01", DAY, 35, 80, None, 600, 300, 0, None)]
    if method == "weight":
        return [WeightRecord(1790812800, 70.5, None)]
    if method == "workouts":
        return [WORKOUT]
    if method == "profile":
        return [PROFILE]
    if method == "workout_detail":
        samples = [WorkoutSample(1790820000 + i, 120 + i, 2.5 * i, 3 * i, 170, 360, None) for i in range(3)]
        track = [TrackPoint(1790820000 + i, 10.0 + i, 20.0) for i in range(3)]
        return [WorkoutDetail(WORKOUT, samples, track if options.get("track") else None)]
    raise AssertionError(f"no canned records for {method}")


class FakeClient:
    """Stands in for ``MiFit``: canned results, the calls recorded as
    ``(method, start, end, options)``, and how many reads overlapped."""

    def __init__(self, session=None):
        self.session = session or make_session()
        self.calls = []
        self.fail = {}
        self.series_points = None
        self.detail_records = None
        self.delay_s = 0.0
        self.most_active = 0
        self._active = 0
        self._guard = threading.Lock()

    def canned(self, method, **options):
        """The result ``method`` returns (for the tests' expectations)."""
        if method in ("heart_rate", "spo2") and self.series_points is not None:
            make = HeartRatePoint if method == "heart_rate" else Spo2Point
            extra = (None,) if method == "heart_rate" else ()
            records = [make(t, v, *extra) for t, v in self.series_points]
        elif method == "heart_rate":
            records = [HeartRatePoint(1790812860, 71, None), HeartRatePoint(1790812800, 69, 1)]
        elif method == "spo2":
            records = [Spo2Point(1790812860, 97)]
        elif method == "workout_detail" and self.detail_records is not None:
            records = list(self.detail_records)
        else:
            records = _records(method, options)
        return DomainResult(records=records, raw=[{"marker": RAW_MARKER}], notes=[])

    def _answer(self, method, start=None, end=None, options=None):
        options = options or {}
        with self._guard:
            self.calls.append((method, start, end, options))
            self._active += 1
            self.most_active = max(self.most_active, self._active)
        try:
            if self.delay_s:
                time.sleep(self.delay_s)
            if method in self.fail:
                raise self.fail[method]
            return self.canned(method, **options)
        finally:
            with self._guard:
                self._active -= 1

    def steps(self, start, end, **options):
        return self._answer("steps", start, end, options)

    def consumption(self, start, end, **options):
        return self._answer("consumption", start, end, options)

    def activity_daily(self, start, end, **options):
        return self._answer("activity_daily", start, end, options)

    def heart_rate_daily(self, start, end, **options):
        return self._answer("heart_rate_daily", start, end, options)

    def sleep(self, start, end, **options):
        return self._answer("sleep", start, end, options)

    def spo2_daily(self, start, end, **options):
        return self._answer("spo2_daily", start, end, options)

    def stress_daily(self, start, end, **options):
        return self._answer("stress_daily", start, end, options)

    def weight(self, start, end, **options):
        return self._answer("weight", start, end, options)

    def workouts(self, start, end, **options):
        return self._answer("workouts", start, end, options)

    def heart_rate(self, start, end, **options):
        return self._answer("heart_rate", start, end, options)

    def spo2(self, start, end, **options):
        return self._answer("spo2", start, end, options)

    def workout_detail(self, start, end, **options):
        return self._answer("workout_detail", start, end, options)

    def profile(self):
        return self._answer("profile")


async def _call(client, name, args, progress):
    callback = None
    if progress is not None:

        async def callback(value, total, message):
            progress.append((value, total, message))

    return await client.call_tool(name, args or {}, progress_callback=callback)


def call_tool(server, name, args=None, *, progress=None):
    """One tool call over an in-process connection to ``server``."""

    async def main():
        async with Client(server) as client:
            return await _call(client, name, args, progress)

    return anyio.run(main)


def call_tools_at_once(server, calls):
    """``calls`` (``(name, args)`` pairs) sent at the same time over one connection;
    the results in the same order."""

    async def main():
        results = [None] * len(calls)
        async with Client(server) as client:
            async with anyio.create_task_group() as group:
                for index, (name, args) in enumerate(calls):

                    async def one(index=index, name=name, args=args):
                        results[index] = await _call(client, name, args, None)

                    group.start_soon(one)
        return results

    return anyio.run(main)


def list_tools(server):
    async def main():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return anyio.run(main)


def server_info(server):
    async def main():
        async with Client(server) as client:
            return client.server_info

    return anyio.run(main)


def text_of(result):
    """Every text block of a tool result, joined."""
    return "".join(block.text for block in result.content if block.type == "text")


def payload(result):
    """The JSON payload of a successful tool result."""
    assert not result.is_error, text_of(result)
    return json.loads(result.content[0].text)
