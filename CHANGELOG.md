# Changelog

All notable changes to mifit are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). While the version is
0.x, a minor version can still change or remove parts of the API; the
"Breaking" list of each version says what.

## 0.3.1 — 2026-10-16

mifit can now be found and installed in two more ways: the official MCP
Registry and a one-click extension for Claude Desktop.

### Added

- mifit is listed in the official MCP Registry as `io.github.Jenwein/mifit`.
  The release workflow publishes `server.json` there after PyPI.
- `mifit-<version>.mcpb`: a Claude Desktop extension attached to each GitHub
  Release. Claude Desktop installs mifit with uv, so no Python setup is needed.
  Its "Allow GPS tracks" switch is off by default.

### Fixed

- The README's registry marker now spells the GitHub owner as the registry
  checks it (`Jenwein`). The registry compares names case-sensitively, so 0.3.0
  could not be listed.

## 0.3.0 — 2026-10-16

mifit now serves your data to AI assistants: `mifit mcp` is a Model Context
Protocol server that runs on your own computer.

### Added

- `mifit mcp`: a read-only MCP server on stdin/stdout for AI assistants (Claude
  Desktop, Claude Code, Cursor, VS Code, ...). One tool per checked domain
  (`get_steps`, `get_calories`, `get_activity`, `get_heart_rate_daily`,
  `get_sleep`, `get_spo2_daily`, `get_stress_daily`, `get_weight`,
  `get_workouts`, `get_heart_rate`, `get_spo2`, `get_workout_detail`), plus
  `get_status` and `get_profile`. Results are the B-layer `records`, never
  `raw`, with local ISO 8601 times. Setup: README, "Use with an AI assistant
  (MCP)".
- Limits per call: 92 days for the daily tools, 31 for `get_calories` (the
  service answers one day per request), 7 for heart-rate and SpO2 samples and
  for sleep stage timelines. Dense results are cut down to at most 500 rows
  (buckets, picked samples, every nth GPS point) and the notes say how. Results
  are kept in memory for 5 minutes.
- `--allow-gps`: GPS tracks stay off unless the server is started with it and
  the tool is asked for one.
- `login_start` / `login_status`: log in by QR code from the assistant, without
  a terminal.

### Changed

- New dependency: `mcp>=2.3,<3`, the official MCP Python SDK (it brings
  pydantic, starlette, uvicorn and others). Only `mifit mcp` imports it.
- `qrcode>=7.4` became `qrcode[png]>=7.4`, for the login QR image.
- `mifit.jsonsafe` holds the CLI's JSON clean-up (records as dicts, NaN as
  `null`), shared with the MCP server.

## 0.2.0 — 2026-10-15

No version was ever tagged or published before this one: `0.1.0` is the version
string of the first commits, which could log in and read steps and raw heart
rate. This entry lists everything since then. The domains were compared with
the Mi Fitness app on one account; the README table says per domain what was
checked.

### Breaking

These matter if you built on the repository before this version. Reading a
result is not affected unless a line below says so.

- `WorkoutSample.altitude_m` is removed. The altitude rebuilt from the file
  matched the app for the ride but not for the run (its extremes were a few
  metres off), so it is not in `records`. It stays in `raw` as the decoded
  fields of the record file: `initialAltitude` on each segment
  (`raw[i]["record"]["segments"][j]`) and `isAltitudeRising` and
  `altitudeChange` on each per-second point (`...["dataPoints"][k]`).
- `WorkoutSample.cadence_spm`: a reported `0` is now `0`, not `None`. The
  app's average cadence counts the seconds with a cadence of `0`. Only an
  invalid value is `None`.
- `SleepRecord.score` is removed. The app shows only a rating, so the number
  could not be checked; it is still in `raw` as `sleep_score`.
- `StepRecord.calories` is renamed `step_calories`: it is the step report's
  walking-calorie figure, not the app's calorie ring.
- New required fields: `Workout.total_kcal` (after `active_kcal`),
  `StressDailySummary.relaxed_min`, `mild_min`, `moderate_min` and
  `severe_min`, and `UserProfile.activity_goal_min` (last). The dataclasses
  have no defaults, so an instance you build by hand needs them (positional
  construction shifts).

### Added

- Data domains, each as `MiFit.<name>()` and `mifit export <name>` (see the
  README table for the fields):
  - `consumption` and `sleep` (with its stage timeline);
  - `heart_rate_daily` (average, maximum, minimum, resting);
  - `workouts` (one entry per workout, with active and total calories);
  - `workout_detail`: per-second samples and the GPS track of outdoor runs and
    rides, read from the encrypted workout files (`--no-track` skips the
    GPS file);
  - `spo2_daily`, `spo2`, `weight` and `stress_daily` (with the minutes in
    each stress band);
  - `activity_daily`: the activity count, activity minutes and active calories
    of the app's 活力指标 page.
- `MiFit.profile()` and `mifit profile`: height, weight and the daily step,
  calorie and activity-time goals.
- Generic reads of any key, uninterpreted: `mifit raw <key>` (with `--daily`
  and `--tag`), `MiFit.raw_window` and `MiFit.daily_report`. The keys known so
  far are in `notes/domain-catalog.md`.
- Long exports are read in chunks: `--chunk day|week|month`, `--long` for
  ranges over 92 days and `--resume` to continue an interrupted export from
  `PATH.partial`. In Python, `MiFit.read_chunks` and `mifit.chunks.merge_chunks`.
- Session renewal: an HTTP 401 renews the session once from the stored
  passToken and retries the request (`MiFit(auto_renew=False)` turns it off;
  `RenewalError` when it cannot). Logins, 401s and renewals are logged to
  `~/.mifit/session-log.jsonl` (event, time and ages only).
- `notes` now say when a read stopped early: the service announced more pages
  but sent no usable cursor, or `heart_rate --full` reached its 10-page cap.
- MIT license, third-party notices, a README, and CI on Linux (Python 3.12 to
  3.14). The `test` extra installs `tzdata`, so the daylight-saving tests also
  run on Windows.

### Changed

- The session file also holds the account's passToken and a device id, which
  renewal needs. A session saved by an earlier version still works but cannot
  renew: run `mifit login` again when it expires.
- `StepRecord.epoch_s` is the local midnight of the date, and the steps
  window no longer depends on the time zone (it was wrong west of UTC).
  `consumption` asks for each day by its local midnight, as the app does, and
  dates it by the local day. The end of a day is the next local midnight minus
  one second, so a day with a clock change is read whole.
- New dependency: `cryptography>=42`, for the workout files.

### Fixed

- Windowed reads of raw data lost the first samples of the window, because
  the service drops its first bucket. `heart_rate`, `spo2`, `weight`,
  `raw_window` and `mifit raw` now ask a day early and keep only your window.
  `MiFit.raw(key, ...)` still returns the service's answer as it came.
- JSON output is strict: NaN and Infinity become `null`, and a note says how
  many were replaced. Data nested too deeply is a clean error, not a
  traceback, and a corrupt session file raises `AuthError`.
- `sleep` skips malformed values with a note instead of raising, prefers the
  duplicate with stages, and gives unstaged sleep no stage minutes instead of
  zero minutes.

### Packaging

- Published on PyPI as `mifit`: install with `uv tool install mifit`,
  `pipx install mifit` or `pip install mifit` instead of cloning the
  repository.
- `mifit --version` and `mifit.__version__`.
- The package is marked as typed (`py.typed`).

## 0.1.0 — 2026-10-14

The first commits; never tagged or published. QR login, the signed transport,
`steps` and `heart_rate`, `MiFit.raw`, and the `mifit login` and `mifit export`
commands.
