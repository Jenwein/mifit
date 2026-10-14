# mifit Sleep Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `sleep` domain: one neutral `SleepRecord` per local day (totals, stages, score, sessions), each session carrying its stage timeline.

**Architecture:** Two reads per call. (1) The daily report `get_aggregated_fitness_data_by_time` (`tag=daily_report`, `key=sleep`) gives each day's totals and its `segment_details` (one per sleep session), windowed with `days.report_window` exactly like steps. (2) The raw record `get_fitness_data_by_time` (`key=sleep`) gives each session's stage `items`; a raw session is attached to the daily session with the same `(bedtime, wake_up_time)`. Day attribution is the service's (the daily report key); the domain never re-buckets sessions itself.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport.get_paged`, `domains.days`, `DomainResult`, `register`), pytest. No new deps.

**Spec:** design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` (§2 anti-goals, §8.1, §12); roadmap `docs/superpowers/roadmap.md` (M2b); evidence `notes/04-mi-health-lab-cloud-breakthrough.md` §2, `notes/06-daily-report-day-semantics.md`; live shape findings below.

## Live-verified shapes (UTC+8 account, 2026-10-14; values below are synthetic)

- Daily row: outer `{tag:"daily_report", key:"sleep", time:<UTC midnight of the local date>, value:<JSON string>, update_time, watermark, source_sid_list, sid}`. Decoded value: `total_duration, sleep_deep_duration, sleep_light_duration, sleep_awake_duration, sleep_score, avg_hr, min_hr, max_hr, avg_spo2, min_spo2, max_spo2, sleep_stage, long_sleep_evaluation, day_sleep_evaluation, segment_details[]`; segment: `bedtime, wake_up_time, timezone, duration, sleep_deep_duration, sleep_light_duration, sleep_awake_duration, awake_count`. `sleep_rem_duration` is in the APK's string table next to these but absent for this device.
- A day holds every session that **ends** on it (a night and a nap both on 10-14). `total_duration` = sum of session `duration` (asleep minutes, awake excluded). A missing measurement is reported as `0` (`avg_spo2: 0`, `avg_hr: 0`).
- Raw row: outer `{key:"sleep", time:<wake_up_time>, zone_offset:28800, zone_name:"Asia/Shanghai", update_time, value, sid}`. Decoded value: `bedtime, wake_up_time, timezone, duration, sleep_*_duration, awake_count, items[]?, is_uncomplete, version, protoTime, device_bedtime, device_wake_up_time, avg_hr?, …`. `items` = `[{start_time, end_time, state}]` (true epoch seconds), contiguous from `bedtime` to `wake_up_time`. A nap had no `items` and `is_uncomplete: true`.
- States, checked against the reported durations on a real night: `2` = deep (105 min), `3` = light (326 min), `5` = awake (51 min). APK constants (notes/04): `0` not asleep, `4` REM, `6` trace.
- Times are true Unix seconds; `timezone` is the recording zone in 15-minute units (`32` → +08:00). The app shifts all times by `timezone*900 - current offset` for display (`FitnessDataUtils.sleepSegmentToLocalTime`); the SDK does **not**: it keeps true instants and exposes the offset.

## Global Constraints

- Read-only; low frequency; no tokens or health values printed, logged or committed. Tests use synthetic fixtures and a fake transport only.
- A + B: `DomainResult.raw` keeps every decoded `value` dict untouched (daily values first, in service order, then raw session values). B models use neutral names; no Mi field names or Mi enum codes inside `models/`.
- Anti-goal (design §2): a field whose meaning is not verified does not go into B. Out of B for now: `sleep_stage`, `long_sleep_evaluation`, `day_sleep_evaluation`, `is_uncomplete`, `version`, `protoTime`, `device_*`, `awake_count`, `source_sid_list`.
- Days: local calendar days in `tz` (machine zone by default), window `report_window(*local_day_span(start, end, tz))`; `SleepRecord.date` = `report_day(row.time)`, `SleepRecord.epoch_s` = `local_midnight(date, tz)` (same as steps/consumption).
- `0` for a heart-rate or SpO2 figure means "not measured" → `None`. A `0` duration is a real value.
- Missing or malformed rows are skipped with a note, never fabricated.

## Review Focus

- **Daily session without a raw match** (raw not yet synced, or times differ): the session must still appear, with `stages == []`, and one note naming the date. → Task 2 test `test_unmatched_session_keeps_no_stages_and_notes_it`.
- **Raw session outside the requested days** (the raw window is widened by a day each side): must not leak into any record or produce a note. → Task 2 test `test_extra_raw_sessions_are_ignored`.
- **Unknown stage code** (e.g. `0`, `6`, `9`): kept as stage `"unknown"`, never dropped or guessed. → Task 2 test `test_unknown_state_maps_to_unknown`.
- **A day with only a nap** (no staged session, no HR/SpO2): record still produced; HR/SpO2 fields `None`, not `0`. → Task 1 test `test_zero_hr_and_spo2_become_none`.
- **Raw row marked `deleted: true`**: not used for stages. → Task 2 test `test_deleted_raw_rows_are_not_used`.

---

### Task 1: Sleep models + daily-report mapping

**Files:**
- Create: `src/mifit/models/sleep.py`, `src/mifit/domains/sleep.py`, `tests/test_domain_sleep.py`, `tests/fixtures/sleep_daily_sample.json`

**Interfaces:**
- Consumes: `Transport.get_paged(endpoint_path, data_obj, *, list_key="data_list", ...) -> list[dict]`; `days.local_day_span`, `days.report_window`, `days.report_day`, `days.local_midnight`; `DomainResult`, `register`.
- Produces (`models/sleep.py`, all `@dataclass`):
  - `SleepStage(start_epoch_s: int, end_epoch_s: int, stage: str)`; `stage` ∈ `"deep" | "light" | "rem" | "awake" | "unknown"`.
  - `SleepSession(start_epoch_s: int, end_epoch_s: int, utc_offset_s: int | None, asleep_min: int, deep_min: int | None, light_min: int | None, rem_min: int | None, awake_min: int | None, stages: list[SleepStage])`.
  - `SleepRecord(date: str, epoch_s: int, asleep_min: int, deep_min: int | None, light_min: int | None, rem_min: int | None, awake_min: int | None, score: int | None, avg_hr_bpm: int | None, min_hr_bpm: int | None, max_hr_bpm: int | None, avg_spo2_pct: int | None, min_spo2_pct: int | None, max_spo2_pct: int | None, sessions: list[SleepSession])`.
  - `domains/sleep.py`: `@register("sleep") def fetch_sleep(t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None) -> DomainResult`. In this task it makes only the daily call; sessions have `stages=[]`.
- Field map (wire → B): day `total_duration→asleep_min`, `sleep_deep_duration→deep_min`, `sleep_light_duration→light_min`, `sleep_rem_duration→rem_min`, `sleep_awake_duration→awake_min`, `sleep_score→score`, `avg_hr/min_hr/max_hr→*_hr_bpm`, `avg_spo2/min_spo2/max_spo2→*_spo2_pct`; session `bedtime→start_epoch_s`, `wake_up_time→end_epoch_s`, `timezone*900→utc_offset_s`, `duration→asleep_min`, the three/four `sleep_*_duration` like the day. Absent → `None`, except `asleep_min`/`start`/`end` which are required (row or session skipped with a note when missing).
- Daily request `data_obj`: `{"tag": "daily_report", "key": "sleep", "reverse": True, "limit": 50, "start_time": …, "end_time": …}`.

- [ ] **Step 1: Write the fixture and failing tests** in `tests/test_domain_sleep.py` (fake transport like `test_domain_steps.py`, dispatching on `endpoint_path`; fixture = two synthetic daily rows: day A with a staged night + a nap, day B with a nap only, `avg_spo2: 0` on B):
  - `test_sleep_domain_is_registered`: `REGISTRY["sleep"] is fetch_sleep`.
  - `test_requests_daily_report_sleep_for_the_window`: with `tz=UTC`, 2025-10-14..15 → `start_time == DAY_1 - 86400`, `end_time == DAY_2`, `tag/key/reverse/limit` as above.
  - `test_maps_day_totals`: day A record has `date`, `epoch_s == local_midnight(date, UTC)`, `asleep_min`, `deep_min`, `light_min`, `awake_min`, `score`, HR/SpO2 equal to the fixture; `rem_min is None` (absent on the wire).
  - `test_maps_sessions_in_service_order`: day A has 2 sessions with `start_epoch_s`/`end_epoch_s` = fixture `bedtime`/`wake_up_time`, `utc_offset_s == 28800` for `timezone: 32`, `asleep_min` = `duration`.
  - `test_zero_hr_and_spo2_become_none`: day B → all `*_hr_bpm`/`*_spo2_pct` that are `0` on the wire are `None`; `asleep_min` stays the wire value.
  - `test_raw_keeps_decoded_daily_values`: `result.raw[:2]` equal the decoded fixture values, untouched.
  - `test_row_with_bad_value_is_skipped_with_note`: a row whose `value` is not JSON → no record, one note.
  - `test_unverified_fields_stay_out_of_records`: no `SleepRecord`/`SleepSession` field named after `sleep_stage`, `*_evaluation`, `awake_count` (assert on `dataclasses.fields` names).
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_sleep.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** the models and `fetch_sleep` (daily call only). Session order = `segment_details` order.
- [ ] **Step 4: Run** the file, then the full suite `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(domains): sleep daily report -> SleepRecord/SleepSession"`

---

### Task 2: Stage timelines from raw sleep sessions

**Files:**
- Modify: `src/mifit/domains/sleep.py`, `tests/test_domain_sleep.py`
- Create: `tests/fixtures/sleep_raw_sample.json`

**Interfaces:**
- Consumes: Task 1's `fetch_sleep`, models.
- Produces: `fetch_sleep` additionally calls `get_paged("data/get_fitness_data_by_time", {"key": "sleep", "start_time": local_midnight(first, tz) - 86400, "end_time": local_midnight(last + 1 day, tz) + 86400, "reverse": True}, list_key="data_list")` and fills `SleepSession.stages`.
- Stage map: `2→"deep"`, `3→"light"`, `4→"rem"`, `5→"awake"`, anything else → `"unknown"`. `start_time`/`end_time` keys; fall back to `start`/`end` (notes/04 saw both spellings).
- Match rule: raw session ↔ daily session when `bedtime` and `wake_up_time` are both equal. Raw rows with `deleted` truthy are ignored. Unmatched raw rows are ignored silently. A daily session with no raw match keeps `stages=[]` and adds one note per affected date ("stage timeline not found"). A matched raw session without `items` (a nap) gives `stages=[]` and **no** note.
- `raw` order: all decoded daily values, then all decoded raw session values (as returned).

- [ ] **Step 1: Write failing tests** (raw fixture: the staged night of day A with 4 items using states 3, 2, 5, 3; day A's nap without items; one extra session a day before the window):
  - `test_requests_raw_sessions_for_the_widened_window`: second call's `data_obj` is `{"key": "sleep", "start_time": local_midnight(first, UTC) - 86400, "end_time": local_midnight(last + 1, UTC) + 86400, "reverse": True}`.
  - `test_attaches_stages_to_the_matching_session`: day A night `stages == [SleepStage(t0, t1, "light"), SleepStage(t1, t2, "deep"), SleepStage(t2, t3, "awake"), SleepStage(t3, t4, "light")]`.
  - `test_nap_without_items_has_no_stages_and_no_note`.
  - `test_unknown_state_maps_to_unknown`: states `0`, `6`, `9` → `"unknown"`.
  - `test_unmatched_session_keeps_no_stages_and_notes_it`: drop the night from the raw fixture → night `stages == []`, exactly one note containing day A's date.
  - `test_extra_raw_sessions_are_ignored`: the out-of-window session appears in no record and causes no note.
  - `test_deleted_raw_rows_are_not_used`: night raw row with `"deleted": true` → night `stages == []` (and the unmatched note).
  - `test_raw_lists_daily_then_session_values`.
- [ ] **Step 2: Run** the file → new tests FAIL.
- [ ] **Step 3: Implement** the raw call, matching and stage mapping.
- [ ] **Step 4: Run** the file and the full suite → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(domains): sleep stage timelines from raw sleep sessions"`

---

### Task 3: Client, CLI and package exports

**Files:**
- Modify: `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (docstring list only), `tests/test_client.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `MiFit.sleep(self, start: DateArg, end: DateArg) -> DomainResult` → `fetch_sleep(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`; `mifit.SleepRecord`, `mifit.SleepSession`, `mifit.SleepStage` exported; `mifit export sleep` works (CLI dispatches via `REGISTRY` + `getattr(client, domain)`; nested dataclasses serialize through `dataclasses.asdict`).

- [ ] **Step 1: Write failing tests:** `test_sleep_delegates_to_domain` (spy on `client_module.fetch_sleep`, local day bounds as for steps); update the registry-listing test to `['consumption', 'heart_rate', 'sleep', 'steps']`; a CLI test that a `SleepRecord` with one session and one stage is written as nested JSON (`records[0]["sessions"][0]["stages"][0]["stage"] == "deep"`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the facade method, imports (the domain import registers it), exports, and add `sleep` to the CLI module docstring list.
- [ ] **Step 4: Run** the full suite → pass; `python -m pip install -e . && mifit export --help` lists `sleep`.
- [ ] **Step 5: Commit** `git commit -m "feat(client,cli): expose sleep"`

---

### Task 4: Live verification (manual, needs the user; not CI)

**Files:**
- Create: `docs/superpowers/verification/2026-10-14-sleep-live.md`
- Modify: `docs/superpowers/roadmap.md` (M2b status), `notes/06-daily-report-day-semantics.md` only if attribution findings change

- [ ] **Step 1:** `mifit export sleep --from <D-3> --to <D> --json "$HOME\mifit-verify\sleep.json"` (outside the repo).
- [ ] **Step 2:** User reads, for 3 nights, from the app's sleep page: total sleep (and whether naps are counted in it or shown apart), deep / light / REM / awake minutes, bedtime and wake-up time, sleep score, sleep average heart rate (and lowest/highest if shown), SpO2 if shown.
- [ ] **Step 3:** Compare field by field. A B field that does not match the app is removed from B (stays in `raw`) or renamed to what it really is; record the decision. Confirm the stage chart's shape against `stages`.
- [ ] **Step 4:** Record pass/partial per field (no health values beyond what the record needs), update the roadmap, commit.

---

## Decisions in this plan that are mine (to confirm when reviewing)

1. **The day is the service's**: a session belongs to the day its daily report lists it under (observed: the day the session ends). No re-bucketing.
2. **REM = state 4** comes from the APK constant only; this device reports no REM, so it cannot be checked live.
3. **Naps are not labeled** in B: the app's night/nap rule is not found yet. A session without stages is just that. Revisit after Task 4 shows how the app presents them.
4. **HR/SpO2 day fields are in B provisionally**; Task 4 keeps or drops them.
5. **`0` HR/SpO2 → `None`**, since 0 bpm / 0 % is not a measurement.
6. **Two calls per `sleep()`**, raw window widened by a day each side and matched on exact times, rather than trusting the raw endpoint's window bounds (unverified, see notes/06).

## Self-Review

1. Spec coverage: roadmap M2b items — daily `sleep` → `SleepRecord` (Task 1), raw stages → `SleepStage` (Task 2), `watch_*` keys left to A/`raw()` (not in scope, stated in roadmap), time semantics (Global Constraints + Task 1/2), live check against the app (Task 4). ✓
2. Step scan: each code step names file, signature and pinned values; tests list names and assertions. ✓
3. Type consistency: `fetch_sleep`, `SleepRecord`/`SleepSession`/`SleepStage` field names identical across Tasks 1–3. ✓
4. Review Focus: five lines, each with a test in Task 1 or 2. ✓
5. Proportion: ~4 tasks for one domain; no code bodies. ✓
