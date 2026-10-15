# mifit MCP Server (P) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship mifit 0.3.0 with `mifit mcp`: a read-only MCP server on stdio that AI clients start with one line (`uvx mifit mcp`). It has one tool per checked data domain, a status tool and a profile tool (P1), and QR-login tools for people who never open a terminal (P2).

**Architecture:**
- **A thin layer on the public API.** The new package `mifit.mcp_server` only calls `MiFit`'s public methods and a few public helpers. No domain changes. The `mcp` SDK is imported only when `mifit mcp` runs.
- **Pure modules first.** Each of these is unit-tested without the SDK:
  - `present.py`: B-layer records → MCP JSON (local ISO times, never `raw`);
  - `resample.py`: buckets, sample picking, track thinning, each capped at 500 rows;
  - `cache.py`: 5-minute LRU cache;
  - `access.py`: one lazily opened client, one lock, re-login pickup;
  - `errors.py`: safe error messages;
  - `tools.py`: the tool table, input checks and descriptions.
- **The SDK layer.** `server.py` turns the table into tools on an `MCPServer`. `login.py` (P2) is a background QR-login state machine.

**Tech Stack:** Python 3.12+, `mcp` 2.x (v2 API: `MCPServer`), anyio (comes with `mcp`), pydantic `Field` for parameter docs, `qrcode[png]` (pypng) for the login QR image, pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-mifit-release-mcp-design.md`:
- §5 (P: the whole MCP design), §7 (security and privacy), §8 (version, CHANGELOG);
- §11 D5–D15;
- §12 F2/F3 (SDK facts; see "Facts rechecked" below).

Read §5 before starting.

## One plan, two parts

P1 (read tools, Tasks 1–7) and P2 (login tools, Tasks 8–9) are in one plan, not two:
- both ship as one release, 0.3.0 (§8);
- P2's tools plug into P1's `server.py`, `access.py` and test fakes;
- the README, CHANGELOG, version bump, final review and release (Tasks 10–11) happen once.

Task 7 is a checkpoint. After it, `mifit mcp` is a complete read-only server. If P2 has to wait, Tasks 1–7 can be reviewed and fast-forwarded into `main` on their own, and Tasks 8–11 continue later.

## Facts rechecked on 2026-10-15 (spec §12 F2/F3)

Checked against `mcp` 2.3.0, installed in a scratch venv; probe scripts drove a real server in-process and over stdio.
- `mcp` 2.3.0 (2026-10-02) is still the latest; its dependencies are as F2 lists them. It installs and imports on Python 3.12 and 3.14 (Windows).
- v2 names that work:
  - `from mcp.server import MCPServer`; `MCPServer(name, version=..., instructions=...)`;
  - `server.add_tool(fn, name=, title=, description=, annotations=, structured_output=False)`;
  - `from mcp.types import ToolAnnotations` (snake_case fields);
  - `from mcp.server.mcpserver import Context, Image`;
  - `from mcp.server.mcpserver.exceptions import ToolError`;
  - `from mcp import Client`. `Client(server)` connects in-process (the tests' connection); `client.server_info` and `client.call_tool(name, args, progress_callback=...)`.
- `mcp.server.fastmcp` raises `ModuleNotFoundError` on purpose.
- Parameters typed `Annotated[T, Field(description=...)]` get their descriptions in the input schema. A parameter annotated `Context` is injected and left out of the schema; this also works in a closure, under `from __future__ import annotations`.
- With `structured_output=False`, a tool has no `outputSchema`.
  - A returned `str` becomes one TextContent.
  - A returned `[str, Image(data=png, format="png")]` becomes TextContent + ImageContent (`image/png`).
- **Errors:**
  - A raised `ToolError("x")` reaches the client as `isError: true` with the text `Error executing tool <name>: x`. It is logged to stderr at INFO, with no traceback.
  - Any other exception reaches the client as only `Error executing tool <name>`. But the SDK logs it to stderr **with the full traceback and the exception's message**. So the server must catch everything itself (decision P-D5).
  - Argument validation errors become ToolErrors that carry pydantic's text.
- `ctx.report_progress(i, n, msg)` reaches a client that passed a `progress_callback`. Without one it is a silent no-op.
- **Threads.** The SDK runs a *sync* tool function on an anyio worker thread (`func_metadata.call_fn`), so F2's open question is answered. The tools here are still `async`, so they can report progress between chunks (spec §5.7).
- **stdio:**
  - A legacy `initialize` (protocolVersion `2025-06-18`), then `notifications/initialized`, then `tools/list`, written to `mifit`'s stdin as JSON lines, gives exactly two JSON-RPC lines on stdout. At stdin EOF the process exits 0.
  - While serving, `stdio_server()` points fd 1 at stderr and fd 0 at the null device. So stray prints, and output from child processes such as a browser launcher, miss the protocol stream.
- **The test guard.** Under the suite's network guard (`tests/conftest.py`), `anyio.run` fails on Windows: asyncio's self-pipe is a loopback `socketpair`, which `connect`s. With a guard that allows loopback addresses only, it works. This is decision P-D6.
- **The QR image.** `qrcode` 8.2 with the `png` extra (pypng, pure Python) writes a PNG: `qrcode.QRCode(..., image_factory=qrcode.image.pure.PyPNGImage)`.
- **Not checked:** whether Claude Desktop shows images in tool results (F3). D14's fallback (a file on disk, opened locally) covers this.

## Decisions this plan adds to the spec

Each can be overruled. They are listed here so the reviewer can check them.

- **P-D1: `get_calories` reads at most 31 days per call, not 92.** The consumption endpoint answers one day per request (`domains/consumption.py`). So 92 days would be 92 paced requests, about 2–2.5 minutes, where spec §5.7 assumed 3–9 requests. 31 days is one chunk, about 35–50 s. Task 11 measures it; if it takes over 45 s, stop and ask the user whether to lower it to 14.
- **P-D2: re-login pickup.** On an `AuthError`, the server loads the stored session again. It retries the read only if that session differs from the one in use (a new `mifit login`, or a renewal by another process). Retrying with the same session would only repeat the 401 and the renewal attempt. This refines §5.6.
- **P-D3: one chunk per worker-thread call.** Chunked reads use `mifit.chunks.plan_chunks` + `merge_chunks` and make one `MiFit` method call per chunk, each through `Access.read` on a worker thread. The calls and the merged records are exactly those of `read_chunks`. In addition, a re-login between chunks is picked up, and progress is reported from async code between chunks.
- **P-D4: the reads lock.** The lock is a `threading.Lock` in `Access`, held for one domain call (one chunk). Two tool calls can interleave chunks, but only one request is ever in flight.
- **P-D5: every tool body maps its own exceptions.**
  - `ToolFailure` → `ToolError(message)`.
  - Anything else → `ToolError("Internal error in mifit (<Type>).")`, and one stderr log line naming the type.
  - This keeps traceback text, which can hold a signed URL, out of the client's server log.
- **P-D6: how the tests drive the server.** Server tests are plain functions that call `anyio.run(...)`, with a new opt-in `allow_loopback` fixture (loopback `connect` only). No pytest async plugin. The global network guard is unchanged.
- **P-D7: buckets are wall-clock slots of the local day** (spec §13 left this open).
  - With interval *k* minutes, the slots are `[00:00, k)`, `[k, 2k)`, …, and they restart at each local midnight.
  - A bucket's `start` is the slot's wall-clock start, with the UTC offset in force then (`fold=0`).
  - On a DST day, the slot containing the change is shorter or longer in real time. A repeated hour falls into one slot.
- **P-D8: workout samples, the automatic step.** The step is the smallest of 1, 5, 10, 15, 30, 60, 120 s that gives ≤ 500 rows. If none does, try 180, 240, 300, … (60 s steps) until one does. Explicit `interval_s` is 1–3600.
- **P-D9: no-network status.** `get_status` reads the stored session with `load_session()` and gives ages only (`session_ages` + `format_age`). It never opens a client.
- **P-D10: login is three steps.**
  - `login_start` mirrors `login_qr`: one `requests.Session` and one device id for the three requests.
  - It drops the server's client on success; the next read opens the newly saved session.
  - It opens the QR file and the confirmation page with `webbrowser.open`.
  - `expired` means the long poll timed out (`poll_qr_login`'s 120 s default).

## Global Constraints

- **Dependencies.**
  - Add `mcp>=2.3,<3` (D5).
  - Change `qrcode>=7.4` to `qrcode[png]>=7.4` (§5.6).
  - Keep `requests>=2.31`, `cryptography>=42` and `requires-python = ">=3.12"` as they are.
  - No new test dependency: anyio comes with `mcp`.
- **The v2 SDK only.** Use the names listed under "Facts rechecked". Never `mcp.server.fastmcp`, and nothing copied from v1 examples.
- **Lazy import.** Only modules under `mifit.mcp_server` import `mcp`. `import mifit` and every other CLI command must not load it.
- **Read-only, public API only.**
  - The MCP layer calls `MiFit` methods plus these helpers: `mifit.chunks.plan_chunks` and `merge_chunks`; `mifit.domains.days.parse_day` and `local_midnight`; `mifit.client.to_epoch_s`; `mifit.auth.qr` (`request_qr_login`, `poll_qr_login`, `exchange_sts`, `new_device_id`); `mifit.region.SID`; `mifit.auth.session.load_session` and `save_session`; `mifit.auth.lifetime` (`session_ages`, `format_age`, `record_event`); `mifit.paths.app_data_dir`.
  - No domain or `MiFit` behaviour changes.
- **Output.**
  - Only `records` and `notes` (or the column blocks for dense data), never `raw`.
  - Field names are the B-layer names. The only renames are `epoch_s` → `time`, `start_epoch_s` → `start` and `end_epoch_s` → `end`; a day record drops `epoch_s`.
  - Each result is compact JSON text (`separators=(",", ":")`) with no NaN, and every tool has `structured_output=False` (D9).
- **Credentials never leave.**
  - No session field is read except through `session_ages`.
  - Error texts are the SDK's own messages, or an exception's type name.
  - The login long-poll URL and the security-confirmation URL never appear in output or logs.
- **GPS.** A track is read only with `--allow-gps` **and** `include_track=true` (D13).
- **Limits (D11 + P-D1).**

  | What | Limit |
  |---|---|
  | daily tools | 92 days |
  | `get_calories` | 31 days |
  | `get_sleep` with `include_stages=true` | 7 days |
  | `get_heart_rate`, `get_spo2` | 7 days |
  | any dense block | ≤ 500 rows / points |

  Over a limit, the call is an error that says how to split it; ranges are never silently cut (D8).
- **Pacing.** Reads are serial: one lock (P-D4), with `Transport` pacing untouched. Successful results are cached for 300 s, 64 entries, LRU, in memory only (D15). `get_status` and the login tools are not cached.
- **Tests stay offline.** `python -m pytest -q` passes after every task. Any test that runs an event loop uses the `allow_loopback` fixture.
- **Public repo.** No health figure or account data in a committed file. Task 11's live outputs go to the session scratchpad only.
- **Line endings.** Every file is LF. Python's `write_text` on this machine writes CRLF, so normalize after any scripted edit. Before each commit, `git diff --check` must be clean and `git ls-files --eol <files>` must show `i/lf`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Ask the user before every push, workflow dispatch and tag push.** A `v*` tag push publishes to PyPI at once: the `pypi` environment has no reviewer.
- **Languages.** README, CHANGELOG, docstrings, tool descriptions and server instructions are in English; the roadmap is in Chinese.

**Precondition:**
- The branch `mcp-server` is cut from `main` after this plan's commit, and the tree is clean.
- Baseline: `python -m pytest -q` gives 1294 passed, 2 skipped on the Windows dev machine (Python 3.14).
- After the final review (Task 10), the branch is fast-forwarded into `main`. Task 11 is the live check and release, with the user.

## Review Focus

- **An AI passing a date in another form** (`yesterday`, `2026/10/07`, `2026-10-07T00:00`). The call must fail, name the `YYYY-MM-DD` form, point to `get_status` for today's date, and read nothing. → Task 5 `test_parse_range_rejects_other_forms`; Task 6 `test_input_errors_read_nothing`.
- **The user logs in again in a terminal while the server runs.** The next call must work without restarting the AI client. → Task 4 `test_auth_error_retries_once_with_a_new_stored_session`.
- **A full week of minute-by-minute heart rate** (10 080 points). It must come back as 30-minute buckets, ≤ 500 rows, with a note saying so. It must not be an error or 10 000 rows. → Task 3 `test_auto_buckets_for_a_full_week_of_minutes`; Task 6 `test_heart_rate_is_bucketed_automatically`.
- **A network error whose text holds a signed URL.** Only `Network request failed (<Type>).` may reach the client and the logs. → Task 4 `test_network_errors_show_only_their_type`; Task 6 `test_no_credential_in_any_output`.
- **A workout start copied with another offset** (`…T02:00:00Z` for a `+08:00` workout). It must find the same workout. → Task 5 `test_workout_start_accepts_any_offset_and_unix_seconds`.

---

## Part 1 (P1): the read tools

### Task 1: Dependencies and `mifit.jsonsafe`

**Files:**
- Modify: `pyproject.toml` (`dependencies`)
- Create: `src/mifit/jsonsafe.py`
- Modify: `src/mifit/cli/__init__.py`: drop `_plain`, `_finite` and `_json_ready`, and use `jsonsafe`.
- Modify: `src/mifit/cli/partial.py`: its docstring names `mifit.jsonsafe.json_ready` instead of `cli._json_ready`.
- Create: `tests/test_jsonsafe.py`. The three `test_finite_*` tests move here from `tests/test_cli.py` and call `jsonsafe.finite`.
- Modify: `tests/test_cli.py` (`test_pyproject_declares_package_dependencies_and_entry_point`)

**Interfaces:**
- Produces, in `mifit.jsonsafe` (D10):
  - `plain(record: Any) -> Any`
  - `finite(value: Any) -> tuple[Any, int]`
  - `nonfinite_note(count: int) -> str`
  - `json_ready(result: DomainResult) -> DomainResult`

  The bodies are the CLI's current ones. `nonfinite_note(n)` returns the CLI's existing text exactly: `f"Replaced {n} non-finite number(s) (NaN or Infinity) with null: strict JSON has no such values."`

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_cli.py`, change the dependency assertions to:
    - `names == {"requests", "qrcode", "cryptography", "mcp"}`;
    - `"mcp>=2.3,<3" in project["dependencies"]`;
    - `"qrcode[png]>=7.4" in project["dependencies"]`.
  - Move the three `test_finite_*` tests into the new `tests/test_jsonsafe.py`, rewritten for `jsonsafe.finite`.
  - Add:

```python
def test_nonfinite_note_is_the_cli_text():
    assert jsonsafe.nonfinite_note(3) == (
        "Replaced 3 non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
    )

def test_json_ready_turns_records_into_dicts_and_counts_replacements():
    result = jsonsafe.json_ready(DomainResult(
        records=[WeightRecord(epoch_s=1, weight_kg=float("nan"), bmi=None)], raw=[], notes=["n"]))
    assert result.records == [{"epoch_s": 1, "weight_kg": None, "bmi": None}]
    assert result.notes == ["n", jsonsafe.nonfinite_note(1)]
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_jsonsafe.py tests/test_cli.py -q`. Expected: `ModuleNotFoundError: mifit.jsonsafe`, and the dependency test fails.

- [ ] **Step 3: Implement.**
  - Create `src/mifit/jsonsafe.py` by moving the three helpers out of the CLI (public names, no leading underscore) and adding `nonfinite_note`.
  - The module docstring says these turn domain results into strict JSON, and that the CLI and the MCP server share them.
  - The CLI imports them (`from mifit.jsonsafe import finite, json_ready, plain` as needed) and keeps its behaviour.
  - Edit the `dependencies` in `pyproject.toml`.

- [ ] **Step 4: Install the new dependencies and run everything.** Run `python -m pip install -e ".[test]"`, then `python -m pytest -q`. Expected: all pass. The count is the baseline plus the two new tests (the three moved tests are not new).

- [ ] **Step 5: Commit** `pyproject.toml`, `src/mifit/jsonsafe.py`, `src/mifit/cli/__init__.py`, `src/mifit/cli/partial.py`, `tests/test_jsonsafe.py` and `tests/test_cli.py` as `refactor: mifit.jsonsafe; depend on mcp and qrcode[png]`.

---

### Task 2: `present.py`: records as MCP JSON

**Files:**
- Create: `src/mifit/mcp_server/__init__.py`. For now only the package docstring: what the server is, and that only this package imports `mcp`.
- Create: `src/mifit/mcp_server/errors.py`, with `ToolFailure` only. Task 4 adds the rest.
- Create: `src/mifit/mcp_server/present.py`
- Create: `tests/test_mcp_present.py`

**Interfaces:**
- Produces:
  - `errors.ToolFailure(Exception)`. Its docstring: a failure whose message is safe to show the AI client; it names the problem and the next step, and never holds a credential.
  - In `present.py`:
    - `offset_label(offset_s: int) -> str`: `"UTC+08:00"`, `"UTC-03:30"`, `"UTC+00:00"`.
    - `local_offset_s(epoch_s: int, tz: tzinfo | None = None) -> int`: the offset of `tz` at that moment, or of the machine's local zone when `tz` is `None`.
    - `iso_time(epoch_s: int, *, offset_s: int | None = None, tz: tzinfo | None = None) -> str`:
      - ISO 8601 to the second, with the offset;
      - it uses `offset_s` when given, else `tz`, else local time.
    - `present_record(record: Any, *, tz: tzinfo | None = None) -> dict[str, Any]`, under the rules below.
    - `without_stages(sleep_day: dict) -> dict`: a copy with the `"stages"` key removed from every item of `"sessions"`.
    - `range_header(tool: str, first: date, last: date, *, tz: tzinfo | None = None) -> dict`:
      - `{"tool", "from", "to", "timezone"}`, with ISO dates;
      - `timezone` is the `offset_label` at `first`'s local midnight (`local_midnight(first, tz)`).
    - `encode(payload: dict[str, Any]) -> str`:
      - it runs `jsonsafe.finite` over the payload;
      - if anything was replaced, it appends `jsonsafe.nonfinite_note(n)` to `payload["notes"]`;
      - it returns `json.dumps(..., separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.

**`present_record` rules:**
- `jsonsafe.plain` the record, then walk the dicts, recursively through dicts and lists.
- **The offset.** A dict with a `utc_offset_s` key that is not `None` uses that offset for its own times and its children's. Otherwise the inherited offset applies; at the top there is none, and times use `tz` / local time.
- **A dict with a `date` key** loses `epoch_s`.
- **Otherwise** `epoch_s` becomes `time`. In every dict, `start_epoch_s` becomes `start` and `end_epoch_s` becomes `end`.
- **The values.**
  - Each renamed value is `iso_time(value, offset_s=<offset>, tz=tz)`, and `None` stays `None`.
  - A renamed key keeps its position in the dict.
  - `utc_offset_s` and every other field are unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_present.py`, with `CST = timezone(timedelta(hours=8))`. Use the CLI tests' synthetic values:

```python
def test_offset_label():
    assert [present.offset_label(s) for s in (28800, -12600, 0, 19800)] == [
        "UTC+08:00", "UTC-03:30", "UTC+00:00", "UTC+05:30"]

def test_iso_time_uses_the_given_offset_before_the_zone():
    assert present.iso_time(1791385920, tz=CST) == "2026-10-07T23:12:00+08:00"
    assert present.iso_time(1790820000, offset_s=0, tz=CST) == "2026-10-01T02:00:00+00:00"

def test_day_record_drops_epoch_s():
    rec = StepRecord(date="2026-10-01", steps=4321, distance_m=None, step_calories=12, epoch_s=1790784000)
    assert present.present_record(rec, tz=CST) == {
        "date": "2026-10-01", "steps": 4321, "distance_m": None, "step_calories": 12}

def test_moment_record_gets_local_time():
    rec = WeightRecord(epoch_s=1790812800, weight_kg=70.5, bmi=None)
    assert present.present_record(rec, tz=CST) == {
        "time": "2026-10-01T08:00:00+08:00", "weight_kg": 70.5, "bmi": None}

def test_workout_uses_its_own_offset_and_keeps_key_order():
    out = present.present_record(replace(WORKOUT, utc_offset_s=0, end_epoch_s=None), tz=CST)
    assert out["start"] == "2026-10-01T02:00:00+00:00" and out["end"] is None
    assert list(out)[:3] == ["start", "end", "utc_offset_s"]
    assert "start_epoch_s" not in out

def test_sleep_sessions_and_stages_use_the_session_offset():
    # SleepRecord(date="2026-10-01", epoch_s=1790784000, ...) with one session
    # (1790812800 -> 1790820000, utc_offset_s=28800) holding
    # SleepStage(1790812800, 1790816400, "deep")
    out = present.present_record(SLEEP_DAY, tz=timezone.utc)
    assert "epoch_s" not in out
    session = out["sessions"][0]
    assert session["start"] == "2026-10-01T08:00:00+08:00"
    assert session["stages"] == [
        {"start": "2026-10-01T08:00:00+08:00", "end": "2026-10-01T09:00:00+08:00", "stage": "deep"}]

def test_stage_without_any_offset_uses_the_zone(): ...       # utc_offset_s=None -> tz used

def test_without_stages_drops_only_the_timelines():
    out = present.without_stages(present.present_record(SLEEP_DAY, tz=CST))
    assert "stages" not in out["sessions"][0] and out["sessions"][0]["deep_min"] == SLEEP_DAY.sessions[0].deep_min

def test_range_header():
    assert present.range_header("get_steps", date(2026, 10, 1), date(2026, 10, 7), tz=CST) == {
        "tool": "get_steps", "from": "2026-10-01", "to": "2026-10-07", "timezone": "UTC+08:00"}

def test_encode_is_compact_and_replaces_nan_with_a_note():
    text = present.encode({"tool": "x", "records": [{"v": float("nan")}], "notes": []})
    assert text == ('{"tool":"x","records":[{"v":null}],"notes":["'
                    + jsonsafe.nonfinite_note(1) + '"]}')
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_present.py -q`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `mcp_server/__init__.py` (docstring only), `errors.py` (`ToolFailure`) and `present.py` to the interfaces and rules above. `present.py` does not import `mcp`.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** the four files as `feat(mcp): present B-layer records as MCP JSON`.

---

### Task 3: `resample.py`: dense data as capped column blocks

**Files:**
- Create: `src/mifit/mcp_server/resample.py`
- Create: `tests/test_mcp_resample.py`

**Interfaces:**
- Consumes: Task 2's `iso_time` and `ToolFailure`.
- Produces, in `resample.py`:
  - Constants: `MAX_ROWS = 500`, `AUTO_BUCKET_MIN = (5, 15, 30, 60, 180)`, `AUTO_STEP_S = (1, 5, 10, 15, 30, 60, 120)`.
  - `slot_start(epoch_s: int, interval_min: int, tz: tzinfo | None) -> datetime`: the P-D7 wall-clock slot start, an aware datetime.
  - `bucket_rows(points: Sequence[tuple[int, int]], interval_min: int, tz) -> list[list]`:
    - one row per non-empty slot, in time order;
    - each row is `[start_iso, min, avg, max, count]`, with `avg` rounded to 1 decimal.
  - `choose_bucket_min(points, tz) -> int`: `0` (raw) when `len(points) <= MAX_ROWS`; otherwise the first value of `AUTO_BUCKET_MIN` whose row count fits, or `180` if none does.
  - `pick(samples: Sequence[T], step_s: int, *, at: Callable[[T], int]) -> list[T]`:
    - it keeps the first sample of each `step_s`-wide slot, counted from the first sample's time: slot `(at(s) - at(samples[0])) // step_s`;
    - samples are taken, never averaged (D12).
  - `choose_step_s(samples, *, at) -> int`: P-D8.
  - `series_block(points: Sequence[tuple[int, int]], *, value: str, what: str, interval_min: int | None, tz) -> tuple[dict, list[str]]`
  - `samples_block(samples: Sequence[WorkoutSample], *, interval_s: int | None, offset_s: int | None, tz) -> tuple[dict, list[str]]`
  - `track_block(points: Sequence[TrackPoint], *, offset_s: int | None, tz) -> tuple[dict, list[str]]`

**The blocks.** Each block is `{"resolution": str, "columns": [...], "rows": [[...], ...]}`, and the rows are in ascending time order (`series_block` sorts its points first).

| Block | Resolution | Columns | Note (exact) |
|---|---|---|---|
| series, raw | `"raw samples"` | `["time", value]` | none |
| series, buckets of *k* | `f"{k} min buckets"` | `["start", f"min_{value}", f"avg_{value}", f"max_{value}", "count"]` | `f"{What} shown as {k} min buckets ({n} samples); ask for a shorter range or a smaller interval_min for more detail."`, where `What` is `what` with only its first letter upper-cased (`what[:1].upper() + what[1:]`): `Heart rate`, `SpO2` (not `str.capitalize`, which gives `Spo2`) |
| samples, step 1 | `"every second"` | `["time", "heart_rate_bpm", "distance_m", "steps", "cadence_spm", "pace_s_per_km", "speed_mps"]` | none |
| samples, step *k* > 1 | `f"every {k} s"` | same | `f"Workout samples shown every {k} s ({kept} of {n} recorded seconds); pass a smaller interval_s for more detail."` |
| track, every point | `"every point"` | `["time", "latitude_deg", "longitude_deg"]` | none |
| track, every *n*th | `f"every {n}th point"` | same | `f"GPS track shown as every {n}th point ({kept} of {total})."` |

**Choosing the resolution:**
- **`series_block`:**
  - `interval_min=None` → `choose_bucket_min`.
  - `0` → raw; more than 500 points raises `ToolFailure(f"{n} raw samples are more than the 500-row limit; omit interval_min or ask for a shorter range.")`.
  - `k > 0` → those buckets; more than 500 rows raises `ToolFailure(f"interval_min={k} gives {rows} rows, more than the 500-row limit; pass a larger interval_min or ask for a shorter range.")`.
- **`samples_block`:**
  - `None` → `choose_step_s`;
  - an explicit step that gives more than 500 rows raises `ToolFailure(f"interval_s={k} gives {rows} rows, more than the 500-row limit; pass a larger interval_s or omit it.")`.
- **`track_block`:** `n = ceil(len / 500)` (at least 1), keeping `points[::n]`.
- **Times:** in the samples and the track they are `iso_time(epoch_s, offset_s=offset_s, tz=tz)`; in the series, `iso_time(..., tz=tz)`, and bucket starts are `slot_start(...).isoformat()`.

- [ ] **Step 1: Write the failing tests.** `CST` as in Task 2; `BERLIN = ZoneInfo("Europe/Berlin")` (the `tzdata` test extra provides it on Windows).

```python
MIDNIGHT = 1791302400                     # 2026-10-07 00:00 +08:00
WEEK = [(MIDNIGHT + 60 * i, 60) for i in range(7 * 1440)]   # a full week of minute samples

def test_bucket_rows_aligned_to_local_midnight():
    pts = [(MIDNIGHT, 60), (MIDNIGHT + 60, 70), (MIDNIGHT + 240, 65), (MIDNIGHT + 300, 80)]
    assert resample.bucket_rows(pts, 5, CST) == [
        ["2026-10-07T00:00:00+08:00", 60, 65.0, 70, 3],
        ["2026-10-07T00:05:00+08:00", 80, 80.0, 80, 1]]

def test_buckets_across_spring_forward():
    pts = [(1774745400, 50), (1774746600, 60)]       # 01:50 +01:00, 03:10 +02:00
    assert [r[0] for r in resample.bucket_rows(pts, 60, BERLIN)] == [
        "2026-03-29T01:00:00+01:00", "2026-03-29T03:00:00+02:00"]

def test_repeated_hour_falls_into_one_slot():
    rows = resample.bucket_rows([(1792888200, 50), (1792891800, 60)], 60, BERLIN)
    assert rows == [["2026-10-25T02:00:00+02:00", 50, 55.0, 60, 2]]

def test_auto_buckets_for_a_full_week_of_minutes():
    assert resample.choose_bucket_min(WEEK, CST) == 30          # 5: 2016, 15: 672, 30: 336
    assert resample.choose_bucket_min(WEEK[:500], CST) == 0

def test_series_block_raw_and_bucketed():
    block, notes = resample.series_block([(MIDNIGHT, 61)], value="bpm", what="heart rate",
                                         interval_min=None, tz=CST)
    assert block == {"resolution": "raw samples", "columns": ["time", "bpm"],
                     "rows": [["2026-10-07T00:00:00+08:00", 61]]} and notes == []
    block, notes = resample.series_block(WEEK, value="bpm", what="heart rate", interval_min=None, tz=CST)
    assert block["resolution"] == "30 min buckets" and len(block["rows"]) == 336
    assert block["columns"] == ["start", "min_bpm", "avg_bpm", "max_bpm", "count"]
    assert notes == ["Heart rate shown as 30 min buckets (10080 samples); ask for a shorter "
                     "range or a smaller interval_min for more detail."]
    _, notes = resample.series_block(WEEK, value="spo2_pct", what="SpO2", interval_min=None, tz=CST)
    assert notes[0].startswith("SpO2 shown as 30 min buckets")

def test_series_block_refuses_an_explicit_interval_that_does_not_fit():
    with pytest.raises(ToolFailure, match="interval_min=5 gives 2016 rows"):
        resample.series_block(WEEK, value="bpm", what="heart rate", interval_min=5, tz=CST)
    with pytest.raises(ToolFailure, match="10080 raw samples"):
        resample.series_block(WEEK, value="bpm", what="heart rate", interval_min=0, tz=CST)

def test_pick_takes_the_first_sample_of_each_slot_across_a_pause():
    t0 = 1790820000
    times = [t0, t0 + 1, t0 + 2, t0 + 7, t0 + 8, t0 + 9]
    assert resample.pick(times, 5, at=lambda t: t) == [t0, t0 + 7]

@pytest.mark.parametrize(("seconds", "step"), [(400, 1), (600, 5), (3600, 10), (72000, 180)])
def test_choose_step_s(seconds, step):
    assert resample.choose_step_s(list(range(seconds)), at=lambda t: t) == step

def test_samples_block_uses_the_workout_offset_and_notes_the_step(): ...
    # 600 one-second WorkoutSamples -> resolution "every 5 s", 120 rows, the first row's
    # time is iso_time(t0, offset_s=28800), the note matches the table above;
    # interval_s=1 -> ToolFailure("interval_s=1 gives 600 rows ...")

def test_track_block_thins_to_500_points():
    pts = [TrackPoint(1790820000 + i, 10.0, 20.0) for i in range(1201)]
    block, notes = resample.track_block(pts, offset_s=28800, tz=CST)
    assert block["resolution"] == "every 3th point" and len(block["rows"]) == 401
    assert notes == ["GPS track shown as every 3th point (401 of 1201)."]
```

  The `"3th"` wording is deliberate: the label is built mechanically from the number, so tests and models can parse it.

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_resample.py -q`.

- [ ] **Step 3: Implement `resample.py`.**
  - `slot_start`:
    - take the local wall-clock time `dt` (`datetime.fromtimestamp(epoch_s, tz)`, or `.astimezone()` when `tz` is `None`);
    - `minute = dt.hour * 60 + dt.minute`, and `slot = minute - minute % interval_min`;
    - the start is `datetime.combine(dt.date(), time(slot // 60, slot % 60), tzinfo=dt.tzinfo)` with `fold=0`.
    - For a `ZoneInfo` this resolves the offset in force at that wall time. With `tz=None`, use the local zone the same way: build the wall time naive, then `.astimezone()`.
  - Rows are ints for min/max/count, and a float for the average.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** both files as `feat(mcp): bucket, pick and thin dense data to 500 rows`.

---

### Task 4: `errors.py`, `access.py` and `cache.py`

**Files:**
- Modify: `src/mifit/mcp_server/errors.py`
- Create: `src/mifit/mcp_server/access.py`, `src/mifit/mcp_server/cache.py`
- Create: `tests/test_mcp_access.py`

**Interfaces:**
- Produces, in `errors.py`:
  - `LOGIN_ADVICE = "Run `mifit login` in a terminal, then try again."` (Task 9 changes this text);
  - `NOT_LOGGED_IN = "Not logged in to Mi Fitness. " + LOGIN_ADVICE`;
  - `GPS_DISABLED = "GPS is disabled on this server; the user can start it with --allow-gps."`;
  - `failure_for(exc: BaseException) -> ToolFailure | None`. Check the cases in this order:

    | Exception | Message |
    |---|---|
    | `RenewalError` | `f"The Mi Fitness session expired and could not be renewed. {exc} {LOGIN_ADVICE}"` |
    | other `AuthError` | `f"Mi Fitness refused the session. {exc} {LOGIN_ADVICE}"` |
    | other `MiFitError` | `str(exc)` (the SDK writes these without secrets) |
    | `requests.RequestException` | `f"Network request failed ({type(exc).__name__})."` |
    | anything else | `None` (the caller re-raises) |

- Produces, in `access.py`: `class Access`:
  - `__init__(self, factory: Callable[[], Any])`: nothing is opened yet.
  - `read(self, fn: Callable[[Any], T]) -> T`:
    - Under one `threading.Lock`: open the client if needed, then return `fn(client)`.
    - **Opening.** It calls `factory()` and keeps the client. An `AuthError` from the factory raises `ToolFailure(NOT_LOGGED_IN)` and keeps nothing.
    - **On an `AuthError` from `fn`** (P-D2), call `factory()` again:
      - if that raises `AuthError`: drop the client and raise `ToolFailure(NOT_LOGGED_IN)`;
      - if the new client's `session.service_token` equals the old one's: raise `failure_for(first error)` and keep the old client;
      - otherwise: keep the new client and return `fn(new)`, which is mapped the same way.
    - Every other exception `e` with `failure_for(e)` not `None` is raised as that failure (`from None`); the rest propagate unchanged.
  - `reset(self) -> None`: drop the client, under the lock. The next `read` opens the stored session again.
- Produces, in `cache.py`: `class TtlCache`:
  - `__init__(self, *, ttl_s: float = 300.0, max_entries: int = 64, clock: Callable[[], float] = time.monotonic)`.
  - `get(self, key: Hashable) -> str | None`: a hit while `clock() - stored_at < ttl_s`; a hit refreshes the entry's recency.
  - `put(self, key: Hashable, value: str) -> None`: (re)stores with the current time and evicts the least recently used entry beyond `max_entries`.
  - No lock: only the event-loop thread uses it.

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_access.py`.
  - `FakeClient(token)` has `.session = SimpleNamespace(service_token=token)` and a `.calls` counter.
  - `SECRET = "SYNTH-SIGNED-URL-SECRET"`.

```python
def test_client_opens_lazily_and_once(): ...                   # factory not called by Access(); one call for two reads

def test_no_session_is_not_logged_in_and_is_not_remembered():
    # factory raises AuthError("No valid Mi Fitness login session ...")
    with pytest.raises(ToolFailure) as err:
        access.read(lambda c: 1)
    assert str(err.value) == errors.NOT_LOGGED_IN
    # second read calls the factory again

def test_auth_error_retries_once_with_a_new_stored_session():
    # factory yields client A (token "A"), then client B (token "B");
    # fn raises AuthError on A and returns "ok" on B
    assert access.read(fn) == "ok"  # and fn ran on A then B; later reads use B

def test_auth_error_with_the_same_session_is_reported_without_a_retry():
    # both factory calls give token "A"; fn raises AuthError("HTTP 401 ...")
    # -> ToolFailure starting "Mi Fitness refused the session. HTTP 401" and ending LOGIN_ADVICE; fn ran once

def test_renewal_error_message():
    assert str(errors.failure_for(RenewalError("Xiaomi refused the passToken (code 70016)."))) == (
        "The Mi Fitness session expired and could not be renewed. Xiaomi refused the "
        "passToken (code 70016). " + errors.LOGIN_ADVICE)

def test_api_error_keeps_its_message(): ...

def test_network_errors_show_only_their_type():
    exc = requests.ConnectionError(f"https://api.example/x?token={SECRET}")
    assert str(errors.failure_for(exc)) == "Network request failed (ConnectionError)."

def test_unknown_errors_propagate():
    with pytest.raises(RuntimeError):
        access.read(lambda c: (_ for _ in ()).throw(RuntimeError("boom")))

def test_reset_opens_the_stored_session_again(): ...

def test_reads_never_overlap():
    # 4 threads call access.read(fn); fn increments an active counter, sleeps 0.02 s, decrements;
    # the maximum seen is 1

def test_cache_hit_until_ttl(clock): ...                    # put at 0; get at 299.9 hit; at 300 miss
def test_cache_evicts_least_recently_used(): ...            # max 2: put a, b; get a; put c -> b gone, a and c kept
def test_cache_put_refreshes_age_and_recency(): ...
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_access.py -q`.

- [ ] **Step 3: Implement the three modules** to the interfaces. `access.py` imports `AuthError` from `mifit.errors`. None of them imports `mcp`.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** as `feat(mcp): serial client access, safe errors and the result cache`.

---

### Task 5: `tools.py`: the tool table, input checks and descriptions

**Files:**
- Create: `src/mifit/mcp_server/tools.py`
- Create: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `ToolFailure`; `mifit.domains.days.parse_day`; `mifit.client.to_epoch_s`.
- Produces:
  - Constants: `DAILY_MAX_DAYS = 92`, `CALORIES_MAX_DAYS = 31`, `SLEEP_STAGES_MAX_DAYS = 7`, `SERIES_MAX_DAYS = 7`.
  - `@dataclass(frozen=True) class DataTool` with fields `name: str`, `method: str`, `kind: Literal["daily", "sleep", "series", "detail"]`, `max_days: int`, `title: str` and `description: str`. `method` is the `MiFit` method and the `mifit.chunks` domain.
  - `DATA_TOOLS: tuple[DataTool, ...]`, in this order:

    | name | method | kind | max_days | must also say |
    |---|---|---|---|---|
    | `get_steps` | `steps` | daily | 92 | `step_calories` is not the calorie ring (use `get_calories`) |
    | `get_calories` | `consumption` | daily | 31 | the most recent day can read lower than the app's calorie ring |
    | `get_activity` | `activity_daily` | daily | 92 | the app's activity page (活力指标) figures |
    | `get_heart_rate_daily` | `heart_rate_daily` | daily | 92 | |
    | `get_sleep` | `sleep` | sleep | 92 | `include_stages=true` adds the stage timelines, at most 7 days |
    | `get_spo2_daily` | `spo2_daily` | daily | 92 | |
    | `get_stress_daily` | `stress_daily` | daily | 92 | the level is a 1–100 index |
    | `get_weight` | `weight` | daily | 92 | records are sparse (only days with a weighing) |
    | `get_workouts` | `workouts` | daily | 92 | pass a workout's `start` to `get_workout_detail` |
    | `get_heart_rate` | `heart_rate` | series | 7 | ≤ 500 rows; `interval_min` |
    | `get_spo2` | `spo2` | series | 7 | ≤ 500 rows; `interval_min` |
    | `get_workout_detail` | `workout_detail` | detail | 0 | outdoor runs and rides only; GPS needs `--allow-gps` and `include_track`; ≤ 500 rows / points |

  - `STATUS_DESCRIPTION`, `PROFILE_DESCRIPTION`. The profile description lists the `UserProfile` fields and says personal details are not given.
  - The parameter documentation strings: `START_DATE_DOC`, `END_DATE_DOC`, `INCLUDE_STAGES_DOC`, `INTERVAL_MIN_DOC`, `WORKOUT_START_DOC`, `INTERVAL_S_DOC`, `INCLUDE_TRACK_DOC`.
  - `parse_range(start_date: str, end_date: str | None, *, max_days: int, tool: str) -> tuple[date, date]`:
    - `end_date=None` means `start_date`;
    - each date must pass `parse_day` and `to_epoch_s` for both its first and last second (as the CLI's `_date_arg` checks).
    - Failures raise `ToolFailure` with exactly these texts:

      | Failure | Text |
      |---|---|
      | not a date | `f"{param} must be a local calendar date in YYYY-MM-DD form, such as 2026-10-07 (got {value!r}); call get_status for today's date."` |
      | reversed range | `"end_date must not be before start_date."` |
      | too long | `f"{tool} reads at most {max_days} days per call; {first}..{last} is {days} days. Split it into {ceil(days / max_days)} calls of up to {max_days} days."` |

  - `parse_workout_start(value: str | int) -> int`:
    - an `int` (not `bool`) or a string of digits is Unix seconds;
    - otherwise the value must be an ISO 8601 date-time with an offset or `Z` (`datetime.fromisoformat`), turned into `int(dt.timestamp())`.
    - Anything else raises `ToolFailure("workout_start must be a workout's start as get_workouts gives it, such as 2026-10-01T10:00:00+08:00, or Unix seconds.")`.

  **Description content.** Each `DataTool.description` (English, a few sentences) says:
  - what it reads;
  - every field of its output, with meaning and unit, worded as the B-layer model docstrings word them;
  - its limit, written as `"<max_days> days"`;
  - its "must also say" item.

  Each daily tool states that days are local calendar days, inclusive. Each description ends with `"Figures were checked against the Mi Fitness app."`

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_tools.py`.

```python
EPOCH = {"epoch_s", "start_epoch_s", "end_epoch_s"}

def _names(*models):
    """The output field names of these models: their fields after present_record's renames."""
    names = set()
    for model in models:
        own = {f.name for f in fields(model)}
        names |= own - EPOCH
        if "start_epoch_s" in own:
            names |= {"start", "end"}
        if "epoch_s" in own and "date" not in own:
            names.add("time")
    return names

FIELDS = {
    "get_steps": _names(StepRecord), "get_calories": _names(ConsumptionRecord),
    "get_activity": _names(ActivityDailySummary), "get_heart_rate_daily": _names(HeartRateDailySummary),
    "get_sleep": _names(SleepRecord, SleepSession, SleepStage), "get_spo2_daily": _names(Spo2DailySummary),
    "get_stress_daily": _names(StressDailySummary), "get_weight": _names(WeightRecord),
    "get_workouts": _names(Workout),
    "get_heart_rate": {"time", "bpm", "start", "min_bpm", "avg_bpm", "max_bpm", "count"},
    "get_spo2": {"time", "spo2_pct", "start", "min_spo2_pct", "avg_spo2_pct", "max_spo2_pct", "count"},
    "get_workout_detail": _names(Workout, WorkoutSample, TrackPoint) | {"workout", "samples", "track"},
}

def test_table_names_methods_and_limits():
    assert [(t.name, t.method, t.kind, t.max_days) for t in tools.DATA_TOOLS] == [...]  # the table above

@pytest.mark.parametrize("tool", tools.DATA_TOOLS, ids=lambda t: t.name)
def test_description_names_every_field_and_the_limit(tool):
    words = set(re.findall(r"[a-z_0-9]+", tool.description))
    assert FIELDS[tool.name] <= words
    if tool.max_days:
        assert f"{tool.max_days} days" in tool.description
    assert tool.description.endswith("Figures were checked against the Mi Fitness app.")

def test_description_limitations():
    d = {t.name: t.description for t in tools.DATA_TOOLS}
    assert "calorie ring" in d["get_calories"] and "outdoor" in d["get_workout_detail"]
    assert "--allow-gps" in d["get_workout_detail"] and "500" in d["get_heart_rate"]
    assert "include_stages" in d["get_sleep"] and "get_workout_detail" in d["get_workouts"]

def test_profile_description_names_the_profile_fields(): ...     # every UserProfile field

def test_parse_range_defaults_end_to_start():
    assert tools.parse_range("2026-10-07", None, max_days=92, tool="get_steps") == (date(2026, 10, 7),) * 2

@pytest.mark.parametrize("value", ["yesterday", "2026/10/07", "2026-10-07T00:00", "2026-02-30", ""])
def test_parse_range_rejects_other_forms(value):
    with pytest.raises(ToolFailure) as err:
        tools.parse_range(value, None, max_days=92, tool="get_steps")
    assert str(err.value).startswith("start_date must be a local calendar date in YYYY-MM-DD form")
    assert "get_status" in str(err.value)

def test_parse_range_order_and_limit():
    with pytest.raises(ToolFailure, match="^end_date must not be before start_date.$"):
        tools.parse_range("2026-10-07", "2026-10-06", max_days=92, tool="get_steps")
    with pytest.raises(ToolFailure) as err:
        tools.parse_range("2026-07-01", "2026-10-01", max_days=92, tool="get_steps")
    assert str(err.value) == ("get_steps reads at most 92 days per call; 2026-07-01..2026-10-01 "
                              "is 93 days. Split it into 2 calls of up to 92 days.")

def test_workout_start_accepts_any_offset_and_unix_seconds():
    for value in ("2026-10-01T10:00:00+08:00", "2026-10-01T02:00:00Z", "2026-10-01T02:00:00+00:00",
                  1790820000, "1790820000"):
        assert tools.parse_workout_start(value) == 1790820000

@pytest.mark.parametrize("value", ["2026-10-01T10:00:00", "yesterday", True, ""])
def test_workout_start_rejects_values_without_a_moment(value): ...
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_tools.py -q`.

- [ ] **Step 3: Implement `tools.py`.** Write the descriptions from the model docstrings (`src/mifit/models/*.py`), the `MiFit` method docstrings and the README's "Known limitations". Do not invent fields or meanings.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** as `feat(mcp): tool table, input checks and descriptions`.

---

### Task 6: `server.py`: the read tools on an `MCPServer`

**Files:**
- Create: `src/mifit/mcp_server/server.py`
- Modify: `tests/conftest.py`: add the opt-in `allow_loopback` fixture, and update the docstring's "Limits of the guard" paragraph to name it.
- Modify: `tests/test_offline.py`: add a test of the fixture.
- Create: `tests/mcp_fakes.py`: `FakeClient`, `SECRETS`, `RAW_MARKER` and the `call_tool` / `list_tools` helpers.
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces, in `server.py`:
  - `SERVER_NAME = "mifit"`.
  - `INSTRUCTIONS`: the exact text below. Task 9 adds one line.
  - `PRIVACY_NOTE = "Tool results are sent to the AI assistant's model provider. The Mi Fitness login stays on this computer; GPS tracks are only available when the server was started with --allow-gps."`
  - `READ_ANNOTATIONS = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)`.
  - `build_server(client_factory: Callable[[], Any] | None = None, *, allow_gps: bool = False, tz: tzinfo | None = None, now: Callable[[], datetime] | None = None, clock: Callable[[], float] = time.monotonic, session_loader: Callable[[], Any] | None = None) -> MCPServer`:
    - `client_factory` defaults to `mifit.MiFit`, looked up when called;
    - `session_loader` defaults to `mifit.auth.session.load_session`;
    - `now` defaults to the aware local now (`datetime.now(tz)` when `tz` is given, else `datetime.now().astimezone()`).
    - It builds `MCPServer(SERVER_NAME, version=mifit.__version__, instructions=INSTRUCTIONS)` and registers 14 tools: `get_status`, `get_profile` and the 12 `DATA_TOOLS`. Every tool has `title`, `description`, `annotations=READ_ANNOTATIONS` and `structured_output=False`.
- Produces, in `tests/conftest.py`: the `allow_loopback` fixture (function scope, not autouse).
  - It saves the real `socket.socket.connect` at conftest import, before any patching.
  - It replaces the guard's `connect` with one that calls the real `connect` only when `ipaddress.ip_address(address[0]).is_loopback`, and otherwise calls `_refuse`.
  - `getaddrinfo` and `create_connection` stay refused.
- Produces, in `tests/mcp_fakes.py`:
  - `SECRETS`: the synthetic `service_token`, `ssecurity`, `pass_token`, `device_id` and extra-cookie value of `FakeClient.session`. Each is a distinct `SYNTH-MCP-…` string.
  - `RAW_MARKER = "SYNTH-RAW-MARKER"`: it is put in every canned `raw`.
  - `P1_TOOLS: frozenset[str]`: `{"get_status", "get_profile"}` plus every `DATA_TOOLS` name. Task 9 adds `P2_TOOLS`. The CLI and packaging tests import these sets too.
  - `FakeClient`:
    - It has every `MiFit` method the tools use.
    - Each returns a canned `DomainResult`: real model records with synthetic numbers, `raw=[{"marker": RAW_MARKER}]` and `notes=[]`. `canned(method) -> DomainResult` gives the result a method returns, for the tests' expectations.
    - It records `(method, start, end, options)` in `.calls`.
    - `fail = {method: exception}` makes a method raise.
    - `series_points` sets what `heart_rate` / `spo2` return.
    - `detail_records` sets what `workout_detail` returns.
  - `call_tool(server, name, args, *, progress=None) -> CallToolResult` and `list_tools(server) -> list[Tool]`. Each runs `anyio.run` around `async with Client(server) as c`. With `progress` (a list), the call passes a `progress_callback` that appends `(progress, total, message)` to it.

**`INSTRUCTIONS` (exact):**

```text
mifit reads the user's own Mi Fitness (Xiaomi) health data from the Xiaomi cloud. All tools are read-only.
Call get_status first: it gives today's local date, the time zone and whether the user is logged in.
Dates are local calendar days in YYYY-MM-DD form; work out "yesterday" or "last week" from get_status.
Start with the daily tools (get_steps, get_sleep, get_heart_rate_daily, ...); use get_heart_rate, get_spo2 and get_workout_detail only when the detail is needed.
Limits per call: 92 days for daily tools (31 for get_calories), 7 days for get_heart_rate, get_spo2 and sleep stages; dense results are cut down to at most 500 rows and the notes say how.
The figures were checked against the Mi Fitness app. Tell the user what the notes say. This is not medical advice.
```

**What each tool does.** In every tool body, the work runs inside `with tool_errors(name):`. That context manager turns `ToolFailure` into `ToolError(str(e)) from None`. Any other `Exception` is logged once with `logging.getLogger("mifit.mcp_server").error("%s failed (%s)", name, type(e).__name__)` and becomes `ToolError(f"Internal error in mifit ({type(e).__name__}).") from None`. Cache keys are tuples starting with the tool name.

| Tool | Parameters (`Annotated[..., Field(description=<tools.*_DOC>)]`) | Body |
|---|---|---|
| daily (8) | `start_date: str`, `end_date: str \| None = None` | `parse_range(..., max_days=t.max_days, tool=t.name)` → cache `(name, first, last)` → `_read_range` → `{**range_header(...), "records": [present_record(r, tz=tz) ...], "notes": result.notes}` |
| `get_sleep` | + `include_stages: bool = False` | as daily, with `max_days = 7 if include_stages else 92` and `tool="get_sleep with include_stages=true"` when stages are on; without stages each record goes through `without_stages`; the key includes `include_stages` |
| series (2) | + `interval_min: Annotated[int \| None, Field(ge=0, le=1440, ...)] = None` | `max_days=7` → `_read_range` → points `(p.epoch_s, p.bpm)` / `(p.epoch_s, p.spo2_pct)` → `series_block(value="bpm"/"spo2_pct", what="heart rate"/"SpO2", ...)` → `{**range_header, **block, "notes": result.notes + block_notes}` |
| `get_workout_detail` | `workout_start: str \| int`, `interval_s: Annotated[int \| None, Field(ge=1, le=3600, ...)] = None`, `include_track: bool = False` | see below |
| `get_profile` | — | cache `("get_profile",)` → `access.read(lambda c: c.profile())` → `{"tool": "get_profile", "records": [...], "notes": [...]}` |
| `get_status` | — | see below; no cache, never opens a client |

- **`_read_range(method, first, last, options, ctx)`** (P-D3):
  - For each chunk of `plan_chunks(method, first, last)`: `await anyio.to_thread.run_sync(access.read, partial(...))`, calling `getattr(c, method)(chunk.first.isoformat(), chunk.last.isoformat(), **options)`.
  - With more than one chunk, after each chunk: `await ctx.report_progress(i, n, f"read {chunk.label}")`.
  - It returns `merge_chunks(parts)`.
- **`get_workout_detail`:**
  - `t = parse_workout_start(...)`.
  - If `include_track` and not `allow_gps`: raise `ToolFailure(GPS_DISABLED)` before reading.
  - `track = allow_gps and include_track`; the cache key is `(name, t, interval_s, track)`.
  - The read is `c.workout_detail(t, t, track=track)` through `access.read`, on a worker thread.
  - **With a record `d`:** the payload is `{"tool", "workout_start": iso_time(t, offset_s=d.workout.utc_offset_s, tz=tz), "timezone": offset_label(d.workout.utc_offset_s or local_offset_s(t, tz)), "workout": present_record(d.workout, tz=tz), "samples": <samples_block or None>, "track": <track_block or None>, "notes": ...}`. The block notes are appended to `result.notes`.
  - **With no record:** the same keys, with `workout`, `samples` and `track` all `None`, and the note `f"No workout starts at {iso_time(t, tz=tz)}; get_workouts gives the start times."`.
- **`get_status`:**
  - It returns `{"tool": "get_status", "version", "today", "timezone", "logged_in", "session", "gps_enabled", "privacy": PRIVACY_NOTE}` (plus `"next_step"` when not logged in).
  - `today` and `timezone` come from `now()`.
  - **With a session:** `logged_in` is true and `session` is `{"login_age", "token_age", "auto_renew"}`, from `session_ages` / `format_age` / `can_renew()`.
  - **No session file** (`None`): `logged_in: false`, `session: null`, `next_step: NOT_LOGGED_IN`.
  - **An unreadable one** (`ValueError`): the same, but `next_step` is `"The stored Mi Fitness session is unreadable. " + LOGIN_ADVICE`.

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_offline.py`:

```python
def test_allow_loopback_lets_an_event_loop_run_but_nothing_else(allow_loopback):
    anyio.run(anyio.sleep, 0)
    with socket.socket() as sock, pytest.raises(RuntimeError, match="network"):
        sock.connect(("192.0.2.1", 443))
```

  - In `tests/test_mcp_server.py`, with `pytestmark = pytest.mark.usefixtures("allow_loopback")` and `CST` as before:
    - every server is `build_server(lambda: fake, tz=CST, now=lambda: NOW, clock=clock, session_loader=lambda: SESSION)`;
    - `NOW = datetime(2026, 10, 15, 9, 30, tzinfo=CST)`;
    - `SESSION` is the fake's session, with `created_at="2026-10-14T01:30:00Z"`.

```python
DAILY_AND_SLEEP = [t for t in tools.DATA_TOOLS if t.kind in ("daily", "sleep")]

def test_identity_and_instructions(): ...         # server.name == "mifit"; Client.server_info.version == mifit.__version__;
                                                   # server.instructions == server_module.INSTRUCTIONS
def test_tools_list():
    listed = {t.name: t for t in list_tools(build())}
    assert set(listed) == P1_TOOLS
    for t in listed.values():
        assert t.description and t.output_schema is None
        a = t.annotations
        assert (a.read_only_hint, a.idempotent_hint, a.open_world_hint) == (True, True, True)

def test_input_schemas():
    s = {t.name: t.input_schema for t in list_tools(build())}
    assert s["get_steps"]["required"] == ["start_date"] and set(s["get_steps"]["properties"]) == {"start_date", "end_date"}
    assert s["get_sleep"]["properties"]["include_stages"]["default"] is False
    assert s["get_workout_detail"]["required"] == ["workout_start"]
    assert s["get_workout_detail"]["properties"]["include_track"]["default"] is False
    assert s["get_status"]["properties"] == {} and s["get_profile"]["properties"] == {}

@pytest.mark.parametrize("tool", DAILY_AND_SLEEP, ids=...)
def test_daily_tools(tool):
    payload = json.loads(call_tool(server, tool.name, {"start_date": "2026-10-01"}).content[0].text)
    assert list(payload) == ["tool", "from", "to", "timezone", "records", "notes"]
    assert payload["records"] == [expected(r) for r in fake.canned(tool.method).records]   # present_record (+ without_stages for sleep)
    assert fake.calls == [(tool.method, "2026-10-01", "2026-10-01", {})]

def test_no_raw_in_any_output(): ...              # every tool once; RAW_MARKER in no text
def test_sleep_stages_only_on_request(): ...      # default: no "stages"; include_stages -> stages; 8 days + stages -> error naming 7
def test_heart_rate_is_bucketed_automatically(): ...   # fake.series_points = a week of minutes -> "30 min buckets"
def test_spo2_raw_when_small(): ...               # "raw samples", columns ["time", "spo2_pct"]
def test_series_interval_errors(): ...            # interval_min=5 over a week -> is_error, "500-row limit"; interval_min=2000 -> is_error

def test_workout_detail_by_start_time():
    out = call(server, "get_workout_detail", {"workout_start": "2026-10-01T10:00:00+08:00"})
    assert fake.calls == [("workout_detail", 1790820000, 1790820000, {"track": False})]
    payload = json.loads(out.content[0].text)
    assert list(payload) == ["tool", "workout_start", "timezone", "workout", "samples", "track", "notes"]
    assert payload["samples"]["columns"][0] == "time" and payload["track"] is None

def test_workout_detail_without_a_workout(): ...  # detail_records = [] -> workout None and the "No workout starts at" note

def test_gps_gate():
    # off: include_track=true -> is_error with GPS_DISABLED in the text, and no call was made
    # off: include_track=false -> called with track=False
    # on (allow_gps=True): include_track=false -> track=False; include_track=true -> track=True,
    #   and payload["track"]["columns"] == ["time", "latitude_deg", "longitude_deg"]

def test_input_errors_read_nothing():
    for name, args in [("get_steps", {"start_date": "yesterday"}),
                       ("get_steps", {"start_date": "2026-07-01", "end_date": "2026-10-01"}),
                       ("get_calories", {"start_date": "2026-09-01", "end_date": "2026-10-02"}),
                       ("get_heart_rate", {"start_date": "2026-10-01", "end_date": "2026-10-08"})]:
        assert call(server, name, args).is_error
    assert fake.calls == []

def test_long_range_is_read_in_chunks_with_progress():
    events = []
    call(server, "get_steps", {"start_date": "2026-07-01", "end_date": "2026-09-30"}, progress=events)
    plan = plan_chunks("steps", date(2026, 7, 1), date(2026, 9, 30))
    assert [c[1:3] for c in fake.calls] == [(c.first.isoformat(), c.last.isoformat()) for c in plan]
    assert [(p, total) for p, total, _ in events] == [(1, 3), (2, 3), (3, 3)]

def test_not_logged_in():
    out = call(build_server(raising_factory(AuthError("No valid ...")), ...), "get_steps", {"start_date": "2026-10-01"})
    assert out.is_error and out.content[0].text == f"Error executing tool get_steps: {errors.NOT_LOGGED_IN}"

@pytest.mark.parametrize(("exc", "text"), [
    (RenewalError("Xiaomi refused the passToken (code 70016)."), "could not be renewed"),
    (ApiError("HTTP 500 from the service."), "HTTP 500 from the service."),
    (requests.ConnectionError(f"https://api.example/?t={SECRETS[0]}"), "Network request failed (ConnectionError)."),
    (RuntimeError(f"boom {SECRETS[1]}"), "Internal error in mifit (RuntimeError)."),
])
def test_read_errors(exc, text, caplog): ...      # caplog.set_level(logging.DEBUG) first; is_error, text in
                                                  # content; neither content nor caplog.text holds a secret

def test_no_credential_in_any_output(caplog):
    # caplog.set_level(logging.DEBUG); every tool once (success), plus each error case above;
    # join every content text, json-dumped list_tools, and caplog.text; no item of SECRETS occurs

def test_cache():
    # get_steps twice with the same args -> one fake call; clock += 301 -> a second call;
    # a failing call followed by the same call -> the client is asked again (errors are not cached)

def test_reads_are_serial(): ...                 # two get_steps (different args) in one task group; a fake
                                                  # method that sleeps 0.05 s and records max concurrency -> 1

def test_get_status_logged_in():
    payload = json.loads(call(server, "get_status", {}).content[0].text)
    assert payload == {"tool": "get_status", "version": mifit.__version__, "today": "2026-10-15",
        "timezone": "UTC+08:00", "logged_in": True,
        "session": {"login_age": "1d00h", "token_age": "1d00h", "auto_renew": True},
        "gps_enabled": False, "privacy": server_module.PRIVACY_NOTE}
    assert factory_calls == 0                     # status never opens a client

def test_get_status_without_or_with_an_unreadable_session(): ...   # loader returns None / raises ValueError
def test_get_profile(): ...                       # records == [present_record(PROFILE)], no raw
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_offline.py tests/test_mcp_server.py -q`.

- [ ] **Step 3: Implement** the fixture, `tests/mcp_fakes.py` and `server.py`.
  - The data tools come from closures, one factory per `kind`, registered from the table with `server.add_tool(fn, name=..., ...)`. Do not write twelve functions (D7).
  - Each closure is `async` and takes `ctx: Context` (left out of the schema).
  - The cache is checked on the event loop before any thread is used, and only successful results are stored.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass. If a test fails with "tests must not use the network" as soon as the event loop starts, the module is missing `allow_loopback`: check `pytestmark`.

- [ ] **Step 5: Commit** as `feat(mcp): MCP server with the read tools`.

---

### Task 7: `mifit mcp`, the stdio check and the release smoke

**Files:**
- Modify: `src/mifit/mcp_server/__init__.py`: add `run`.
- Modify: `src/mifit/cli/__init__.py`: the `mcp` subcommand, and the module docstring.
- Modify: `.github/workflows/release.yml`: one more step in the `smoke` job.
- Create: `tests/test_mcp_cli.py`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: `build_server`.
- Produces:
  - `mifit.mcp_server.run(*, allow_gps: bool = False) -> None`:
    - it imports `server` inside the function (lazy import);
    - it writes one line to stderr: `f"mifit {mifit.__version__}: MCP server on stdio (GPS tracks {'on' if allow_gps else 'off'})"`;
    - then `build_server(allow_gps=allow_gps).run()`.
  - The CLI command `mifit mcp [--allow-gps]`:
    - help `"run the MCP server for AI assistants (stdio, read-only)"`;
    - `--allow-gps` help `"let get_workout_detail return GPS tracks when asked (they show where you were)"`;
    - the handler does `from mifit import mcp_server`, calls `mcp_server.run(allow_gps=args.allow_gps)` and returns 0.

- [ ] **Step 1: Write the failing tests.**
  - Add to `tests/test_mcp_cli.py` (`ROOT`, `SRC` and `env = {**os.environ, "PYTHONPATH": str(SRC)}` as in `test_cli.py`):

```python
HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]

def test_mcp_command_passes_the_gps_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(mcp_server, "run", lambda *, allow_gps: seen.append(allow_gps))
    assert cli.main(["mcp"]) == 0 and cli.main(["mcp", "--allow-gps"]) == 0
    assert seen == [False, True]

def test_stdout_carries_only_json_rpc():
    proc = subprocess.run([sys.executable, "-m", "mifit.cli", "mcp"],
        input="".join(json.dumps(m) + "\n" for m in HANDSHAKE),
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2] and all(r["jsonrpc"] == "2.0" for r in replies)
    init = replies[0]["result"]
    assert (init["serverInfo"]["name"], init["serverInfo"]["version"]) == ("mifit", mifit.__version__)
    assert init["instructions"] == INSTRUCTIONS                    # from mifit.mcp_server.server
    assert {t["name"] for t in replies[1]["result"]["tools"]} == P1_TOOLS   # from mcp_fakes
    assert "MCP server on stdio (GPS tracks off)" in proc.stderr

def test_mifit_and_its_cli_do_not_import_the_sdk():
    code = "import sys, mifit, mifit.cli; sys.exit('mcp' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, timeout=60).returncode == 0
```

  - Add to `tests/test_release_workflow.py`:

```python
def test_smoke_runs_the_mcp_server_over_stdio():
    runs = _runs(JOBS["smoke"])
    assert '["mifit", "mcp"]' in runs and "tools/list" in runs and "get_status" in runs
```

  The subprocess test needs no network: `tools/list` reads no data. The home directory is the test's own, through conftest's environment.

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_cli.py tests/test_release_workflow.py -q`.

- [ ] **Step 3: Implement `run` and the CLI command.**
  - Add `mcp` to the CLI module docstring's list of commands, with one sentence: what it serves; that it is read-only; that GPS tracks need `--allow-gps`.
  - Add a smoke step after "The command and the package report the built version":

```yaml
      - name: The MCP server answers over stdio
        run: |
          python - <<'EOF'
          import json, subprocess
          msgs = [
              {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
              {"jsonrpc": "2.0", "method": "notifications/initialized"},
              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
          ]
          proc = subprocess.run(["mifit", "mcp"], input="".join(json.dumps(m) + "\n" for m in msgs),
                                capture_output=True, text=True, timeout=120)
          assert proc.returncode == 0, proc.stderr
          replies = [json.loads(line) for line in proc.stdout.splitlines()]
          assert [r["id"] for r in replies] == [1, 2], replies
          names = {t["name"] for t in replies[1]["result"]["tools"]}
          assert "get_status" in names, names
          EOF
```

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass. Then run actionlint on `release.yml` (`uvx --from actionlint-py actionlint .github/workflows/release.yml`) and fix what it reports.

- [ ] **Step 5: Try the server by hand.** Run `python -m mifit.cli mcp`, paste the three handshake lines and press Ctrl+Z, Enter (Windows) to end stdin. You should see two JSON lines and a clean exit.

- [ ] **Step 6: Commit** as `feat(mcp): mifit mcp command; stdio check in tests and release smoke`.

**Checkpoint (P1 complete).** `mifit mcp` is a working read-only server. If P2 is deferred, Task 10's README/CHANGELOG steps can be done for P1 alone. Then drop `login_start` / `login_status` from the README tool table and leave `LOGIN_ADVICE` as it is.

---

## Part 2 (P2): the login tools

### Task 8: `login.py`: the background QR-login flow

**Files:**
- Create: `src/mifit/mcp_server/login.py`
- Create: `tests/test_mcp_login.py`

**Interfaces:**
- Consumes: from `mifit.auth.qr`, `request_qr_login`, `poll_qr_login`, `exchange_sts` and `new_device_id`; `mifit.region.SID`; `save_session`; `record_event`; `app_data_dir`.
- Produces:
  - `LOGIN_QR_FILE_NAME = "login-qr.png"`.
  - `@dataclass(frozen=True) class LoginView`, with fields:
    - `status: Literal["idle", "pending", "succeeded", "failed", "confirmation_required", "expired"]`;
    - `message: str`;
    - `login_url: str | None = None`;
    - `png: bytes | None = None`.

    `login_url` and `png` are set only while the status is `pending`.
  - `qr_png(content: str) -> bytes`: a PNG, error correction L, border 2, built with `PyPNGImage`.
  - `class LoginFlow`:
    - `__init__(self, *, on_success: Callable[[], None], qr_path: Path | None = None, opener: Callable[[str], Any] = webbrowser.open, spawn: Callable[[Callable[[], None]], None] = <start a daemon thread>, request=request_qr_login, poll=poll_qr_login, exchange=exchange_sts, save=save_session, record=record_event, http_factory: Callable[[], Any] | None = None)`.
      - `qr_path` defaults to `app_data_dir() / LOGIN_QR_FILE_NAME`.
      - `http_factory` defaults to `requests.Session`.
    - `start(self) -> LoginView`. It blocks for the QR request and holds an internal lock throughout.
      - **Already pending:** it returns the current view and sends nothing.
      - **Otherwise:** a new `http`, `device_id = new_device_id()` and `challenge = request(http, SID, device_id=device_id)`.
        - **If the QR request fails,** the view becomes `failed`, with the message rules below.
        - **If it succeeds:**
          - `png = qr_png(challenge.login_url)`;
          - write it to `qr_path`, creating the parent with `mode=0o700`, then `opener(qr_path.as_uri())`; both are best effort: an `OSError` or an opener returning false only adds `" (The QR image could not be opened on this computer.)"` to the message;
          - set the view to `pending`;
          - `spawn(job)`, where `job` runs `poll(challenge, http, device_id=device_id)`, then `exchange(result, SID, http, device_id=device_id)`, `save(session)`, `record("login", session)` and `on_success()`.
        - It returns the view.
    - `view(self) -> LoginView`.
  - The challenge (with its `lp_url`) lives only in the job's local variables.
  - When a job ends, in any way, it deletes `qr_path` (`missing_ok`; an `OSError` is ignored) and sets the final view.

**The messages (exact; the job's outcome decides the view):**

| Outcome | status | message |
|---|---|---|
| started | `pending` | `"Scan the QR code with the Xiaomi or Mi Fitness app and approve the login, or open login_url on a phone signed in to the Xiaomi account. Then call login_status."` |
| saved | `succeeded` | `"Logged in. The session is saved on this computer; the data tools work now."` |
| `NotificationRequiredError` | `confirmation_required` | `"Xiaomi asks for a security confirmation. A confirmation page was opened in the browser on this computer; complete it, then call login_start again."`. If `opener(exc.notification_url)` raised or returned false: `"Xiaomi asks for a security confirmation, but the page could not be opened on this computer. Run `mifit login` in a terminal instead."` |
| `requests.Timeout` from `poll` | `expired` | `"The QR code expired before the login was approved. Call login_start for a new one."` |
| other `requests.RequestException` | `failed` | `f"Login failed: network request failed ({type(exc).__name__})."` |
| `MiFitError` | `failed` | `f"Login failed: {exc}"` |
| `OSError` from `save` | `failed` | `f"Login failed: could not save the session ({exc.strerror or type(exc).__name__})."` |
| anything else | `failed` | `f"Login failed ({type(exc).__name__})."` |
| no login yet | `idle` | `"No login in progress. Call login_start to log in."` |

- [ ] **Step 1: Write the failing tests** in `tests/test_mcp_login.py`.
  - Fakes: `request` returns `QrChallenge(login_url=LOGIN_URL, qr_payload=QR_URL, lp_url=LP_URL)`, where `LP_URL` contains `"SYNTH-LP-SECRET"`.
  - `spawn` appends the job to `jobs`, so a test runs it by hand.
  - `opener` records its targets and returns `True`.
  - `poll` / `exchange` / `save` / `record` are fakes that record their arguments.

```python
def test_start_shows_the_qr_and_starts_one_job(tmp_path):
    view = flow.start()
    assert view.status == "pending" and view.login_url == LOGIN_URL and view.png.startswith(b"\x89PNG")
    assert (tmp_path / "login-qr.png").read_bytes() == view.png
    assert opened == [(tmp_path / "login-qr.png").as_uri()] and len(jobs) == 1

def test_a_second_start_while_pending_reuses_the_qr():
    first, second = flow.start(), flow.start()
    assert second == first and request_calls == 1 and len(jobs) == 1

def test_concurrent_starts_send_one_request():   # request sleeps 0.05 s; two threads call start; one request

def test_success_saves_records_and_switches(tmp_path):
    flow.start(); jobs[0]()
    assert saved == [SESSION] and recorded == [("login", SESSION)] and switched == [True]
    assert flow.view() == LoginView("succeeded", SUCCESS_MESSAGE)
    assert not (tmp_path / "login-qr.png").exists()
    assert poll_args[0][1] is exchange_args[0][2]      # one http client for the whole login

def test_confirmation_opens_the_page_locally_and_hides_the_url():
    # poll raises NotificationRequiredError("https://account.xiaomi.com/synthetic/confirm?SYNTH-NOTE-SECRET")
    # -> opened[-1] is that URL; status confirmation_required; no field of the view holds "SYNTH-NOTE-SECRET"

def test_long_poll_timeout_is_expired(): ...       # poll raises requests.ReadTimeout -> expired
def test_failures(): ...                           # ApiError / ConnectionError / save OSError / RuntimeError -> the table's messages
def test_qr_request_failure_is_failed_and_starts_nothing(): ...
def test_a_finished_login_can_start_again(): ...   # after expired, start() sends a new request
def test_the_long_poll_url_is_never_in_a_view():   # every view seen in all tests above: LP_URL in none of
                                                   # repr(view), view.message, view.login_url
def test_unopenable_qr_only_adds_a_remark(): ...   # opener returns False -> still pending; the message ends with the remark
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcp_login.py -q`.

- [ ] **Step 3: Implement `login.py`.** It does not import `mcp`.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** as `feat(mcp): background QR login flow`.

---

### Task 9: `login_start` and `login_status` tools

**Files:**
- Modify: `src/mifit/mcp_server/server.py`, `src/mifit/mcp_server/errors.py`
- Modify: `tests/test_mcp_server.py`, `tests/test_mcp_cli.py`, `tests/mcp_fakes.py`

**Interfaces:**
- Consumes: `LoginFlow`, `LoginView`; `Access.reset`.
- Produces:
  - `build_server(..., login_factory: Callable[[Callable[[], None]], Any] | None = None)`. `build_server` calls `login_factory(access.reset)` once, so the flow's `on_success` is the server's own `Access.reset`; the result needs `start()` and `view()`. The default is `lambda on_success: LoginFlow(on_success=on_success)`. (A ready-made flow cannot be passed in: it would need the server's `Access` before the server exists.)
  - `LOGIN_ANNOTATIONS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)`.
  - `errors.LOGIN_ADVICE` becomes `"Run `mifit login` in a terminal or call login_start, then try again."`.
  - `INSTRUCTIONS` gains, after the `get_status` line: `If the user is not logged in, call login_start and show them the QR code it returns.`.
  - `get_status` gains `"login": login.view().status`, after `"session"`.

**The two tools** (no parameters, not cached, `structured_output=False`):

| Tool | Description must say | Body |
|---|---|---|
| `login_start` | it logs the user in to Mi Fitness by QR code on this computer; it returns the QR image and a link; the user scans it with the Xiaomi or Mi Fitness app; the QR file is also opened on this computer; it is only needed when `get_status` says the user is not logged in | `view = await anyio.to_thread.run_sync(login.start)`; text = `encode({"tool": "login_start", "status", "message", "login_url"})`; return `[text, Image(data=view.png, format="png")]` when `view.png`, else `text` |
| `login_status` | it reports the QR login started by `login_start` | `encode({"tool": "login_status", "status", "message"})` |

- [ ] **Step 1: Write the failing tests.**
  - `tests/mcp_fakes.py` gains:
    - `P2_TOOLS = P1_TOOLS | {"login_start", "login_status"}`;
    - `FakeLogin`: `start()` / `view()` return set `LoginView`s and count the calls. Servers get it through `login_factory=lambda on_success: fake_login`.
  - The default `build()` helper in `tests/test_mcp_server.py` now passes a `FakeLogin` (view `idle`), so no test can start a real login.
  - `test_get_status_logged_in`'s expected dict gains `"login": "idle"` after `"session"`.
  - In `tests/test_mcp_server.py`:

```python
def test_login_tools_listed_with_write_annotations():
    listed = {t.name: t for t in list_tools(build())}
    assert set(listed) == P2_TOOLS
    for name in ("login_start", "login_status"):
        a = listed[name].annotations
        assert (a.read_only_hint, a.destructive_hint, a.open_world_hint) == (False, False, True)

def test_login_start_returns_text_and_the_qr_image():
    out = call(server, "login_start", {})
    assert [c.type for c in out.content] == ["text", "image"] and out.content[1].mime_type == "image/png"
    assert json.loads(out.content[0].text)["login_url"] == LOGIN_URL

def test_login_status_and_get_status_show_the_login(): ...  # FakeLogin view "succeeded" -> both report it

def test_success_switches_the_client():
    # login_factory=lambda on_success: LoginFlow(on_success=on_success, <Task 8's fakes>,
    #     spawn=lambda job: job())                       # the job runs at once
    # client factory raises AuthError until the fake save ran, then returns the FakeClient;
    # get_steps -> NOT_LOGGED_IN (mentions login_start); login_start; get_steps -> records

def test_not_logged_in_mentions_login_start(): ...
```

  - Also add `login_start` and `login_status` to the `test_no_credential_in_any_output` sweep, with `LP_URL` and the notification URL in the secret list.
  - In `tests/test_mcp_cli.py`, the expected tool set becomes `P2_TOOLS`.

- [ ] **Step 2: Run them; they fail.**

- [ ] **Step 3: Implement** the two tools, the `login_factory` parameter with its default, and the `errors` and `INSTRUCTIONS` changes.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Commit** as `feat(mcp): login_start and login_status`.

---

## Part 3: 0.3.0

### Task 10: README, CHANGELOG, version and roadmap; final review

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml` (`version = "0.3.0"`; add `"mcp"` and `"model-context-protocol"` to `keywords`), `docs/superpowers/roadmap.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `load_release_tool()` (`tests/support.py`). The repository test `test_main_against_this_repository` (`tests/test_release_tool.py`) requires the CHANGELOG to have a `## 0.3.0` section once `pyproject` says `0.3.0`, so bump both in this task.

- [ ] **Step 1: Write the failing tests** in `tests/test_packaging.py`:

```python
MCP_NAME_MARKER = "<!-- mcp-name: io.github.jenwein/mifit -->"

def test_readme_carries_the_mcp_registry_marker():
    assert _readme().count(MCP_NAME_MARKER) == 1

def test_readme_mcp_section():
    section = _section(_readme(), "Use with an AI assistant (MCP)")
    for text in ("uvx mifit mcp", "claude mcp add --scope user mifit -- uvx mifit mcp",
                 "claude_desktop_config.json", ".cursor/mcp.json", ".vscode/mcp.json",
                 "--allow-gps", "model provider", "login_start", "uvx mifit login"):
        assert text in section, text
    for name in P2_TOOLS:                 # the 16 tool names, from mcp_fakes
        assert f"`{name}`" in section, name

def test_version_is_0_3_0_with_its_changelog():
    assert _pyproject()["project"]["version"] == "0.3.0"
    notes = release.changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "0.3.0")
    assert "mifit mcp" in notes and "--allow-gps" in notes and "mcp>=2.3,<3" in notes
```

  The existing `test_readme_links_are_absolute` must keep passing: every new link is absolute.

- [ ] **Step 2: Run them; they fail.**

- [ ] **Step 3: Edit `README.md`.**
  - The marker goes on its own line after the badges.
  - Add `## Use with an AI assistant (MCP)` between "## Log in" and "## Export". It holds, in order:
    1. **Privacy first** (spec §7):
       - tool results go to the AI client's model provider;
       - the login stays in `~/.mifit` on this computer;
       - the server is read-only and opens no network port;
       - GPS tracks are off unless the server is started with `--allow-gps`, and a track shows where you were.
    2. **Log in once** with `uvx mifit login` (or `mifit login`), or let the assistant call `login_start`.
    3. **Configs:** the `claude mcp add` line and the three JSON snippets from spec §6.1. Then one sentence: after `uv tool install mifit`, the command is `mifit` with the argument `mcp`. Then how to add `--allow-gps`.
    4. **A table of the 16 tools:** name, what it reads, limit per call. Use the limits from this plan (with `get_calories` 31 days).
    5. **One line:** "Ask in your own words; the assistant picks the tools", with an example question.

- [ ] **Step 4: Add the CHANGELOG `## 0.3.0 — 2026-10-15` section** above 0.2.0:
  - **Added:**
    - `mifit mcp`, the MCP server (read-only, stdio), with the tool list in one line;
    - `--allow-gps`;
    - `login_start` / `login_status`;
    - downsampling to ≤ 500 rows, and the per-call limits.
  - **Changed:**
    - the dependencies: `mcp>=2.3,<3` (which pulls in pydantic, starlette, uvicorn, …), and `qrcode[png]>=7.4`;
    - `mifit.jsonsafe` now holds the CLI's JSON clean-up, shared with the MCP server.

  Task 11 sets the real date.

- [ ] **Step 5: Roadmap (Chinese).** The P row becomes "实现完成，待发布 0.3.0（计划 [2026-10-15-mifit-mcp-server](plans/2026-10-15-mifit-mcp-server.md)）".

- [ ] **Step 6: Run `python -m pytest -q`.** Expected: all pass. Then rebuild locally into the scratchpad, as R Task 3 Step 5 did:
  - `uvx --from build pyproject-build --outdir "$SCRATCH/dist"`, `uvx twine check --strict` and `python tools/release.py check-dist`;
  - install the wheel into a fresh `uv venv` (Python 3.12) and run the smoke step's handshake script there against that venv's `mifit`.

- [ ] **Step 7: Commit** as `docs: README MCP section, CHANGELOG and version 0.3.0`.

- [ ] **Step 8: Final review and merge.**
  - One fresh reviewer, on the most capable model, reads the whole branch against spec §5, §7 and this plan, including the "Decisions this plan adds" list.
  - Fix the findings.
  - Fast-forward `mcp-server` into `main`, and ask the user before pushing.

---

### Task 11: Live check and release of 0.3.0 (with the user)

There is no new code here. Every outward step waits for the user's go-ahead. Live outputs and figures go to the scratchpad only, and the report gives only "equal", field names or durations.

- [ ] **Step 1 (me): consistency and timing on the real account.**
  - Write a scratch script. It builds `build_server()` with the real `MiFit` and calls the tools in-process. The range ends yesterday, 7 days for daily tools and 1 day for the dense ones.
  - For each daily domain, it compares the `records` with `mifit export <domain>` for the same range (the CLI's JSON), field by field, after converting the CLI's epoch fields with `present_record`'s rules.
  - It times `get_calories` over 31 days and `get_heart_rate` over 7 days.
  - Report "equal" or the differing field names, and the durations.
  - If `get_calories` over 31 days takes more than 45 s, stop and ask the user about 14 days (P-D1).

- [ ] **Step 2 (user, with my help): connect the dev build to their AI client.**
  - Claude Code: `claude mcp add --scope user mifit-dev -- python -m mifit.cli mcp`, using the dev environment's `python` with its full path.
  - Claude Desktop: the same as JSON.
  - Then go through spec §5.12 with the user:
    1. `get_status` works;
    2. 3–5 questions in their own words (for example "我上周每天睡了多久", "昨天心率最高是几点", "最近一次跑步的配速变化"), checking which tools the assistant chose and whether the answers make sense;
    3. P2: with the user's OK, rename `~/.mifit/session.json` to `session.json.bak`, log in through `login_start` in the client, then delete or restore the backup as the user decides;
    4. without `--allow-gps`, ask for a route, and check it is refused.
  - Write down what the user saw (no figures) in the scratchpad. Fix findings in the normal way: a test first, then a commit.

- [ ] **Step 3 (me, after the user agrees): push `main`.** `CI` must be green.

- [ ] **Step 4 (me, optional, after the user agrees): TestPyPI dry run.**
  - Run `gh workflow run release.yml --ref main`, then `gh run watch`.
  - Expected: `test`, `build`, `smoke` (now with the MCP handshake on three systems) and `publish-testpypi` are green.

- [ ] **Step 5 (me): the release date.** Set `## 0.3.0 — <today>` in the CHANGELOG. Commit as `docs: 0.3.0 release date`, and push with the user's go-ahead.

- [ ] **Step 6 (me, after the user agrees): tag and push.**

```bash
git tag -a v0.3.0 -m "mifit 0.3.0"
git push origin v0.3.0
```

  Expected:
  - every job is green except `publish-testpypi`, which is skipped;
  - <https://pypi.org/project/mifit/0.3.0/> exists, and its README has the MCP section (the marker comment is not shown);
  - the GitHub Release `v0.3.0` exists.

- [ ] **Step 7 (me): check from PyPI.**
  - Run the smoke handshake script against `uvx mifit@0.3.0 mcp`, which should list 16 tools.
  - The user switches the client config from `mifit-dev` to `uvx mifit mcp` (README) and calls `get_status` once.

- [ ] **Step 8 (me): close out.**
  - Roadmap: P becomes "✅ 0.3.0 已发布（<date>）"; E becomes "待计划".
  - Memory: update `release-and-mcp`: P is done; E is next; the README marker is in 0.3.0.
  - Commit, and push with the user's go-ahead.
