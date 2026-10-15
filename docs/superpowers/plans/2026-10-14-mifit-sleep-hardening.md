# mifit Sleep Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the eight Minor findings of the sleep review (roadmap §2, ①–⑧) and take the unverified `score` out of the B layer, so `sleep()` never raises on odd data, never lets one bad read sink the whole call, and `raw` holds exactly the data behind the records.

**Architecture:** All changes are in `src/mifit/domains/sleep.py` (plus one field removed from `models/sleep.py`). Parsing helpers stop raising on non-finite or oddly typed values (Task 1). The raw-session read is reshaped around one internal `_RawSession` per `(bedtime, wake_up_time)`: deleted rows never enter, a staged session beats an unstaged duplicate, every session gets its own copy of the stages, and only the raw values actually attached to a session go to `raw` (Task 2). The raw request is skipped when there is nothing to attach to, and an `ApiError` from it becomes a note instead of an exception, as in `consumption` (Task 3).

**Tech Stack:** Python 3.12+, pytest, existing mifit modules. No new deps.

**Spec:** roadmap `docs/superpowers/roadmap.md` §2 row "sleep 审查遗留 Minor" (①–⑧); design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` §2 anti-goal ("无法确证含义的字段不进 B") and §8.1; plan `docs/superpowers/plans/2026-10-14-mifit-sleep-domain.md` (Global Constraints: "Missing or malformed rows are skipped with a note, never fabricated"); verification `docs/superpowers/verification/2026-10-14-sleep-live.md` (score not verifiable); partial-failure precedent `src/mifit/domains/consumption.py` (`ApiError` → note, anything else propagates).

## Findings checked at plan time (current code on `t1-compliance` HEAD)

- ① Raises today: `int(inf)` → `OverflowError`, not caught in `_to_session`, the raw key in `_attach_stages`, or `_to_stages`; `_minutes(nan)` → `ValueError`, not caught anywhere (`avg_hr: NaN` sinks the call); `_STAGES.get([2])` → `TypeError` (unhashable); `segment_details: 5` → `TypeError`, `"abc"` → one note per character. `json.loads` accepts `NaN`/`Infinity`.
- ② `timelines[key] = ...`: the last raw row with a key wins, so an unstaged duplicate after a staged one wipes the stages.
- ③ Every decodable raw row is appended to `raw` before the `deleted` check and before matching: deleted rows (their `deleted` flag lives on the outer row, so it is lost) and the widened window's neighbours both end up in `raw`.
- ④ `session.stages = stages` hands the same list (and the same `SleepStage` objects) to every session with those times.
- ⑤ The raw request is sent even when no record has a session.
- ⑥ The only raw-window test uses `tz=UTC`. CI's machine zone is UTC and the dev machine's is UTC+8, so a test in either zone cannot catch a dropped `tz`; a third zone can.
- ⑦ In `test_extra_raw_sessions_are_ignored`, `starts` is built from daily sessions, which never come from raw rows, so `assert ... not in starts` cannot fail.
- ⑧ `consumption` catches `ApiError` per day and notes it; `AuthError` and `requests` exceptions propagate. `sleep()` catches nothing around the raw read.
- ⑨ `SleepRecord.score` maps `sleep_score`. The live check could not see a number (the app shows only a rating), and the score is also present on a nap-only day (live: a score was present on a nap-only day; the fixture has one too), where what the app would show is unknown.

## Global Constraints

- Start from `main` after T1 is merged (README at repo root). Independent of the T4 CI plan; either order.
- Read-only; tests offline with synthetic fixtures and `FakeTransport` only. Baseline `python -m pytest -q`: 273 passed, 2 skipped (no `tzdata`), or T4's count if that plan landed first. This plan adds 20 tests (+10, +5, +5, +0).
- A + B: B fields carry only verified meanings (design §2). Notes are English sentences; a note about a day contains its `YYYY-MM-DD`.
- Partial failure: an `ApiError` from a sub-request becomes one note and the rest of the result stands; `AuthError`, `requests` exceptions and programming errors propagate (same split as `consumption`).
- Unknown or unreadable stage codes are `"unknown"`, never dropped or guessed. A malformed row/session/item is skipped with a note, never fabricated.
- LF line endings (`core.autocrlf=input`); scripted edits on Windows write bytes or `newline="\n"`.

## Review Focus

- **The raw read fails mid-export** (service error on the second request): the days already read must come back, with one note. → Task 3 `test_failed_raw_request_keeps_the_days_and_notes_it`.
- **A device that sends stage codes of an unexpected type** (string, list, object): stage `"unknown"`, no crash. → Task 1 `test_unhashable_state_maps_to_unknown` (lists/objects); strings already map to `"unknown"`.
- **The same session reported twice** (two raw rows, or two daily sessions with the same times): stages kept, objects independent, `raw` lists the raw value once. → Task 2 `test_duplicate_raw_session_without_stages_does_not_hide_the_staged_one`, `test_sessions_with_identical_times_do_not_share_stages`.
- **A session the user deleted in the app** (`deleted: true` raw row): neither used for stages nor shown in `raw`. → Task 2 `test_deleted_raw_rows_stay_out_of_raw` (+ existing `test_deleted_raw_rows_are_not_used`).
- **A zone other than the machine's or UTC** (traveller, server in another zone): the raw window follows `tz`. → Task 3 `test_raw_window_follows_the_requested_zone`.

---

### Task 1: Malformed values are skipped, never raised (①)

**Files:**
- Modify: `src/mifit/domains/sleep.py` (`_minutes`, `_to_session`, `_to_record`, `_to_stages`, `_attach_stages`; new `_stage_name`, `_RawSession`)
- Test: `tests/test_domain_sleep.py`

**Interfaces:**
- Produces (private, used by Tasks 2–3):
  - `class _RawSession(NamedTuple): value: dict; stages: list[SleepStage]; skipped: int` — one decoded raw session; `skipped` counts its unreadable stage items.
  - `_to_stages(items: object) -> tuple[list[SleepStage], int]` — `(stages, skipped)`. `items` absent/`None` → `([], 0)` (a nap); any other non-list → `([], 1)`; inside a list, a non-dict item or one whose start/end is missing, non-numeric or non-finite counts as skipped.
  - `_stage_name(state: object) -> str` — `_STAGES[state]` for an `int` (not `bool`) key in `_STAGES`, else `"unknown"`.
  - `_attach_stages` keeps `timelines: dict[tuple[int, int], _RawSession]`; when a session is matched and `skipped > 0`, it appends `f"Skipped {n} unreadable stage item(s) of a sleep session on {record.date}."`.
- Rules: `_minutes` returns `None` for a non-finite float (`math.isfinite`); every `int(...)` on a wire time/duration catches `OverflowError` beside `KeyError`/`TypeError`/`ValueError`. `segment_details` absent/`None` → no sessions, no note; a list → as today; anything else → no sessions and one note `f"Skipped the sessions of {day}: they are not a list."` (the record itself is kept). Raw rows whose value is not decodable stay silently ignored (Decision 1b).
- Test helpers added to the test module: `edit_value(payload: dict, index: int, change: Callable[[dict], None]) -> dict` (decode row `index`'s `value`, apply `change`, re-encode with `json.dumps`, return `payload`); indices `DAILY_A = 1`, `RAW_NIGHT = 2`, `RAW_EXTRA = 3`; `EXTRA_BED = DAY_A - 86400 + 2 * 3600`.

- [ ] **Step 1: Write the failing tests** (each builds on `load(...)` + `edit_value`, then `fetch(...)`; `inf = float("inf")`, `nan = float("nan")`):
  - `test_non_finite_session_time_skips_the_session_with_a_note`: daily A `segment_details[0]["bedtime"] = inf` → day A sessions are `[nap]` (`start_epoch_s == NAP_A_BED`); `len(result.notes) == 1` and `"2025-10-14" in result.notes[0]`.
  - `test_non_finite_figures_become_none`: daily A `avg_hr = nan`, `sleep_deep_duration = inf`, `segment_details[0]["timezone"] = nan` → `record.avg_hr_bpm is None`, `record.deep_min is None`, `(record.light_min, record.awake_min) == (280, 40)`, `night.utc_offset_s is None`.
  - `test_raw_session_with_non_finite_times_is_not_matched`: raw night `bedtime = inf` → `night.stages == []`, exactly one note, containing `"2025-10-14"`.
  - `test_unhashable_state_maps_to_unknown`: raw night `items[0]["state"] = [2]`, `items[1]["state"] = {"x": 1}` → `[s.stage for s in night.stages] == ["unknown", "unknown", "awake", "light"]`; `result.notes == []`.
  - `test_segment_details_not_a_list_is_noted`, parametrized over `5`, `"abc"`, `{"bedtime": 1}` as daily A `segment_details` → day A record exists with `sessions == []`; `len(result.notes) == 1`; the note contains `"2025-10-14"` and `"not a list"`.
  - `test_unreadable_stage_items_are_skipped_with_a_note`, parametrized over replacing raw night `items[1]` with `{"start_time": inf, "end_time": T2, "state": 2}`, `"junk"`, `{"state": 2}` → `night.stages == [SleepStage(T0, T1, "light"), SleepStage(T2, T3, "awake"), SleepStage(T3, T4, "light")]`; one note containing `"2025-10-14"` and `"1 unreadable stage item"`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_sleep.py -q` → the 10 new tests FAIL (`OverflowError`/`ValueError`/`TypeError`, or wrong note count); the old ones pass.
- [ ] **Step 3: Implement** the Interfaces above in `src/mifit/domains/sleep.py` (`import math`, `from typing import NamedTuple`). `_to_stages` builds each stage with `_stage_name(item.get("state"))`.
- [ ] **Step 4: Run** the file, then `python -m pytest -q` → baseline + 10 passed.
- [ ] **Step 5: Commit** `git commit -m "fix(sleep): skip malformed values with a note instead of raising"`

---

### Task 2: Raw sessions: duplicates, deleted rows, neighbours, shared stages (② ③ ④ ⑦)

**Files:**
- Modify: `src/mifit/domains/sleep.py` (`_attach_stages`, module docstring), `tests/test_domain_sleep.py`

**Interfaces:**
- Consumes: Task 1's `_RawSession`, `_to_stages`, `timelines`.
- Produces (behaviour later tasks and callers rely on):
  - Rows with a truthy `deleted` never enter `timelines` or `raw`.
  - Choosing among raw rows with the same key: the first in service order wins, except that a row with stages replaces a kept row without stages (②).
  - Each matched `SleepSession.stages` is a new list of new `SleepStage` objects (`dataclasses.replace`) (④).
  - `DomainResult.raw` = decoded daily values (service order), then the `value` of each `_RawSession` that matched at least one daily session, once each, in the order its key first appeared in the raw rows (③). Nothing else from the raw read goes to `raw`.
  - Module docstring states this and that `MiFit.raw("sleep", start, end)` returns every raw session as served.

- [ ] **Step 1: Write the failing tests / fix the existing ones:**
  - `test_duplicate_raw_session_without_stages_does_not_hide_the_staged_one`, parametrized `position` in `("before", "after")`: insert a copy of the raw night row with `items` removed at index `RAW_NIGHT` (before) or `RAW_NIGHT + 1` (after) → `night.stages` equals the four stages of `test_attaches_stages_to_the_matching_session`; `result.notes == []`.
  - `test_duplicate_staged_raw_sessions_keep_the_first`: insert at `RAW_NIGHT + 1` a copy whose `items == [{"start_time": T0, "end_time": T4, "state": 3}]` → `night.stages` is the original four; the copy's decoded value is not in `result.raw`.
  - `test_deleted_raw_rows_stay_out_of_raw`: raw night row `"deleted": True` → its decoded value not in `result.raw`.
  - `test_sessions_with_identical_times_do_not_share_stages`: append a copy of daily A's night segment to its `segment_details` → `a, _, b = day_a.sessions`; `a.stages == b.stages`; `a.stages is not b.stages`; `a.stages[0] is not b.stages[0]`; after `b.stages.clear()`, `len(a.stages) == 4`; the night's raw value occurs in `result.raw` exactly once.
  - Replace `test_raw_lists_daily_then_session_values` with `test_raw_holds_daily_values_then_the_matched_sessions`: `result.raw == [decoded daily rows 0, 1] + [decoded raw rows 0, 1, 2]` (the extra row 3 is absent).
  - Rewrite `test_extra_raw_sessions_are_ignored` (⑦): `extra = decoded raw row RAW_EXTRA`; `extra not in result.raw`; no stage of any session starts at `EXTRA_BED`; `result.notes == []`. Drop the `starts` assertion.
- [ ] **Step 2: Run** the file → FAIL: the `after` case, keep-the-first, deleted-out-of-raw, identity, the replaced raw test and the rewritten extra test.
- [ ] **Step 3: Implement** the Produces rules in `_attach_stages`; update the module docstring's `raw` paragraph.
- [ ] **Step 4: Run** the file, then `python -m pytest -q` → previous + 5 passed.
- [ ] **Step 5: Commit** `git commit -m "fix(sleep): prefer staged duplicates, copy stages, raw holds only matched sessions"`

---

### Task 3: The raw request: skip it, survive it failing, follow `tz` (⑤ ⑥ ⑧)

**Files:**
- Modify: `src/mifit/domains/sleep.py` (`_attach_stages`, `fetch_sleep` docstring), `tests/test_domain_sleep.py`

**Interfaces:**
- Consumes: Task 2's `_attach_stages`.
- Produces:
  - No raw request when no record has a session (`not any(r.sessions for r in result.records)`) (⑤).
  - `ApiError` from the raw `get_paged` → no stages anywhere, `raw` = daily values only, and exactly one note `f"Stage timelines were not read: the service returned an error{suffix}. Sleep sessions have no stages."` with `suffix = f" (code {err.code})"` when `err.code is not None`, else `""`; no per-date "not found" notes. Any other exception propagates (⑧).
  - Test double: `FakeTransport(daily=None, raw=None, raw_error: Exception | None = None)` records the call, then raises `raw_error` for `RAW_ENDPOINT` when set; `fetch(daily=None, raw=None, raw_error=None)` passes it through.

- [ ] **Step 1: Write the failing tests:**
  - `test_no_raw_request_without_sessions`, parametrized over a daily payload `{"result": {"data_list": []}}` and the daily fixture with every `segment_details` set to `[]` → `len(transport.calls) == 1` (the daily call).
  - `test_failed_raw_request_keeps_the_days_and_notes_it`: `raw_error=ApiError("boom", code=500)` → `[r.date for r in result.records] == ["2025-10-15", "2025-10-14"]`; day A still has 2 sessions; every session's `stages == []`; `result.raw` == the two decoded daily values; `len(result.notes) == 1`; the note contains `"stage"` (case-insensitive) and `"code 500"`.
  - `test_auth_error_on_raw_request_propagates`: `raw_error=AuthError("rejected")` → `pytest.raises(AuthError)`.
  - `test_raw_window_follows_the_requested_zone` (⑥): `est = timezone(timedelta(hours=-5))`, `start = DAY_A + 5 * 3600` (2025-10-14 00:00 at -05:00), `end = start + 2 * 86400 - 1`; `fetch_sleep(transport, start, end, tz=est)` → `calls[0]["data_obj"]` window is `(DAY_A - 86400, DAY_B)` (daily keys are UTC; unchanged) and `calls[1]["data_obj"]` has `start_time == DAY_A + 5 * 3600 - 86400`, `end_time == DAY_A + 5 * 3600 + 3 * 86400`.
- [ ] **Step 2: Run** the file → the ⑤ and ⑧ tests FAIL. The ⑥ test PASSES (it pins current behaviour); check it can fail by temporarily dropping `tz` from the raw window's `local_midnight` calls, then restore.
- [ ] **Step 3: Implement** the Produces rules (`from mifit.errors import ApiError`); add one sentence each to `fetch_sleep`'s docstring for the skipped request and the noted failure.
- [ ] **Step 4: Run** the file, then `python -m pytest -q` → previous + 5 passed.
- [ ] **Step 5: Commit** `git commit -m "fix(sleep): skip the raw read without sessions; note a failed raw read"`

---

### Task 4: `score` leaves the B layer (⑨); README, verification note, roadmap

**Files:**
- Modify: `src/mifit/models/sleep.py` (remove `SleepRecord.score` and its mention in the docstring), `src/mifit/domains/sleep.py` (drop the mapping; add `sleep_score` to the docstring's raw-only list), `tests/test_domain_sleep.py`, `tests/test_cli.py` (`SLEEP` constant: drop `score=70`), `README.md` ("Two layers" bullet), `docs/superpowers/verification/2026-10-14-sleep-live.md` (one appended bullet), `docs/superpowers/roadmap.md` (§2 sleep row only)

**Interfaces:**
- Produces: `SleepRecord(date, epoch_s, asleep_min, deep_min, light_min, rem_min, awake_min, avg_hr_bpm, min_hr_bpm, max_hr_bpm, avg_spo2_pct, min_spo2_pct, max_spo2_pct, sessions)`; the score stays reachable as `raw[i]["sleep_score"]`. Breaking for `0.1.0` users who read `.score`; acceptable before any release.

- [ ] **Step 1: Write the failing test:** in `test_unverified_fields_stay_out_of_records` add `"score"` to the tuple of unverified names; in `test_maps_day_totals` replace `assert record.score == 72` with `assert result.raw[1]["sleep_score"] == 72`.
- [ ] **Step 2: Run** the file → `test_unverified_fields_stay_out_of_records` FAILS (`score` is a field).
- [ ] **Step 3: Implement** the removal in model, domain and `tests/test_cli.py`. In README "Two layers", the `records` bullet becomes (wrapped at ~78 columns like its neighbours):

```markdown
- **`records`** are typed dataclasses with neutral names (`asleep_min`,
  `avg_hr_bpm`, ...). A field's meaning is established from what the app
  displays or from the app's own code before it enters this layer. One, the
  REM stage, rests on the app's code alone, because the author's device
  reports no REM. The sleep score is not in this layer: the app showed only a
  rating, so the number could not be checked; it is in `raw` as `sleep_score`.
  A value the service did not report is `None`, never a made-up `0`.
```

Append to the verification doc's "Decisions taken from this run" (`<日期>` = today):

```markdown
- **Superseded (<日期>, sleep hardening):** `score` moved out of B; it is in `raw` only (`sleep_score`). The design §2 anti-goal wins over the name-based reading, and the score is also reported for a nap-only day, where its meaning in the app is unknown.
```
- [ ] **Step 4: Run** `python -m pytest -q` → previous count, all pass (no new tests in this task); `grep -rn "\.score\|score=" src tests` → nothing.
- [ ] **Step 5: Roadmap §2 sleep row** — replace only that row (`<日期>` = today) with:

```markdown
| ~~sleep 审查遗留 Minor ①–⑧~~ → **已修（sleep 加固，<日期>）**：①畸形数据跳过并记 note（不可哈希的 state 记为 unknown）；②相同起止的重复原始会话优先取有分期的；③`raw` 只含日汇总和实际匹配上的原始会话（已删除、加宽窗口带出的不再混入；全部原始会话用 `MiFit.raw("sleep", …)`）；④分期列表按会话复制；⑤没有会话时不发原始请求；⑥补 UTC−5 原始窗口测试；⑦重写该测试使断言可失败；⑧原始请求的 ApiError 保留日汇总并记 note，AuthError 照常抛出。另：`score` 移出 B 层，只留在 `raw`（`sleep_score`）：app 只显示等级，数值未核对（设计 §2 反目标）。 | — | ✅ sleep 加固 |
```
- [ ] **Step 6: Commit**

```bash
git add src/mifit/models/sleep.py src/mifit/domains/sleep.py tests/test_domain_sleep.py tests/test_cli.py README.md docs/superpowers/verification/2026-10-14-sleep-live.md docs/superpowers/roadmap.md
git commit -m "fix(sleep)!: score is raw-only until verified; roadmap: sleep Minor items fixed"
```

---

## Decisions

Item by item (all eight are fixed; none is won't-fix):

1. **① Fix.** A single odd value must not sink a multi-day export. (a) A non-hashable or non-integer `state` becomes `"unknown"` without a note, because the existing rule is "any other code → unknown" and an odd code is still a reported stretch of time. (b) Raw rows whose `value` does not decode stay silently ignored: the raw read spans neighbour days, and a daily session that loses its match already gets the "not found" note. (c) Non-list `segment_details` keeps the day (its totals are fine) with one note, not one per character/key.
2. **② Fix:** staged beats unstaged; otherwise first in service order wins (deterministic; two different staged copies of one session were never observed). Not by `update_time`: unverified ordering semantics.
3. **③ Fix, by narrowing `raw`** to the raw values actually attached to a session, rather than filtering by our own day rule (the domain never re-buckets; plan 2026-10-14 Decision 1). This drops deleted rows and neighbours together, and keeps `raw` = "the data behind these records". Unmatched raw sessions inside the requested days are dropped from this domain's `raw` too; `MiFit.raw("sleep", …)` is the complete A-layer read of that key, and the README already says `raw` is not a full dump.
4. **④ Fix:** copy list and `SleepStage` objects per session (dataclasses are mutable).
5. **⑤ Fix:** no sessions → nothing to attach → no request (less traffic, design §3). Consistent with ③.
6. **⑥ Fix (test only):** fixed UTC−05:00 offset, no `tzdata` needed; chosen because it is neither CI's zone (UTC) nor the dev machine's (UTC+8), so a dropped `tz` fails on both.
7. **⑦ Fix (test only):** assert on `raw` and on stage starts, both of which a regression can break.
8. **⑧ Fix, `consumption`'s split:** `ApiError` → one note, daily records kept; `AuthError` (session problem, not about this read) and `requests` exceptions propagate. Retry is not added (transport-level policy).
9. **⑨ `score` moves to `raw` only.** The design §2 anti-goal is a failure condition ("出现即算失败"); the live check saw no number; and the field is reported for a nap-only day, so even its meaning (night score? day score?) is open, not just its value. The verification doc's earlier "stays in B" is superseded with a dated bullet. Restoring it is one field once someone with visible scores matches it. **REM stays in B:** state 4 comes from the same APK `sleepState` table whose 2/3/5 were verified live, and `sleep_rem_duration` sits beside verified siblings; it is `None` on the verified device and the README says it rests on the app's code alone.
10. Four tasks, grouped by the code they touch (parsing / matching / request / model), so a reviewer can reject one without the others.

## Self-Review

1. Spec coverage: ① Task 1; ②③④⑦ Task 2; ⑤⑥⑧ Task 3; ⑨ + roadmap §2 row Task 4. Each has a fix/won't-fix line in Decisions. ✓
2. Step scan: tests name their inputs and assertions; code steps give private signatures, rules and note copy; no bodies. ✓
3. Type consistency: `_RawSession(value, stages, skipped)`, `_to_stages -> (stages, skipped)`, `_stage_name`, `FakeTransport(..., raw_error=...)`, `edit_value`, `RAW_NIGHT`/`RAW_EXTRA`/`DAILY_A`/`EXTRA_BED` used identically in Tasks 1–3; `SleepRecord` without `score` in Task 4 matches `tests/test_cli.py`. ✓
4. Review Focus: five lines, each with a named test in Tasks 1–3. ✓
5. Proportion: four tasks, about one test per behaviour, no implementation bodies. ✓
