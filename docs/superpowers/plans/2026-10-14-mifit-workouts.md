# mifit Workout List (Sport Records Summary) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `workouts` domain: one neutral `Workout` per recorded workout (sport, start/end, duration, distance, active calories, average/maximum heart rate, steps), with every `SportBasicReport` passed through untouched in `raw`.

**Architecture:** One cursor-paged read of `data/get_sport_records_by_time` (`list_key="sport_records"`, `reverse=true`, `limit=50`) through `Transport.get_paged_result`, capped at 20 pages with a note when the cap cuts the read short. A workout is selected by its start instant: the request asks from one second before the caller's start (the service excludes `start_time`), and rows are also filtered client-side on the outer `time`. The Mi sport-type number → neutral sport name table lives in the domain module (the mapping layer, design §12.2) and covers only the types the account actually has; any other type keeps its source number and gets `sport=None`.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport.get_paged_result`/`PagedResult`, `DomainResult`, `register`, `as_int`), pytest. No new deps.

**Spec:** roadmap `docs/superpowers/roadmap.md` (§3 M4, §6, §7); design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` (§2 anti-goals, §6 入口二, §8.1, §12); evidence `notes/04-mi-health-lab-cloud-breakthrough.md` §2(c), `notes/06-daily-report-day-semantics.md` (same exclusive-start rule); `reference/mi-fitness-sync/docs_mi-fitness-activity-findings.md` (background only; two of its claims are wrong, see Decisions 13); the APK and live findings below.

**Precondition:** M3 (branch `m3-hr-daily`) is merged into `main`. This plan uses `Transport.get_paged_result` / `PagedResult` (M3 Task 3), and `notes/08` is taken, so the new notes file is `notes/10`. Work goes straight onto `main`, or onto a short branch fast-forwarded into it.

## Live-verified shapes (UTC+8 account, 2026-10-14, 6 read-only calls; all values below are synthetic)

**Request** (APK `persist.server.data.GetSportReportByTime`; wire names read from its `@SerializedName`s)
- `{category?, start_time, end_time, reverse, next_key?, limit}`, times in Unix seconds. The app's own pull (`CloudSyncLineRouter.pullSportReportByTime` → 3-arg constructor) sends `category=null`, `reverse=true`, `limit=50`.
- Upstream Mi-Fitness-Sync sends `startTime`/`endTime`. Those are not the wire names; upstream then filters by start time on the client.

**Response** (`result` envelope, unwrapped by `get_paged_result`)
- `{"sport_records": [...], "has_more": false, "next_key": ""}`.
- Outer row (every key on every live row): `{"sid": "synthetic", "key": "outdoor_running", "category": "running", "time": 1760436000, "value": "<JSON string>", "zone_name": "Asia/Shanghai", "zone_offset": 28800, "update_time": 1760440000, "watermark": 1}`. `key` and `category` are Mi labels and can differ (`outdoor_running` vs `running`). The APK row class also declares `deleted` and `cheat_info`; neither appeared live.
- Rows came newest first.

**Decoded `value`** (`SportBasicReport`; 38–72 keys per live row, no nested objects)
- On every row: `sport_type`, `proto_type`, `time`, `timezone`, `start_time`, `end_time`, `duration`, `calories`, `total_cal`, `avg_hrm`, `max_hrm`, `min_hrm`, `version`, plus sport-specific extras (zones `hrm_*_duration`, training effect/load, VO2max, pace, speed, cadence…).
- `distance` only on distance sports (running, riding); `steps` only on running. Absent, never `0`, on the others.
- Relations, held on every live row:
  - outer `time` == value `start_time`. Value `time` was `start_time` or `start_time − 1`, so it is not used.
  - outer `zone_offset` == value `timezone × 900` (`timezone` counts quarter hours).
  - `end_time − start_time − duration` is 1 s, or several minutes on a run with a pause.
  - `avg_pace` (s/km) and `avg_speed` (km/h) agree with `distance / duration`. So `distance` is metres, `duration` is seconds of moving time, and pauses are not in `duration`.
  - `total_cal − calories` grows with elapsed time at roughly a resting-metabolism rate. So `calories` is the active energy (kcal) and `total_cal` adds resting energy.
  - `min_hrm ≤ avg_hrm ≤ max_hrm`. The `hrm_*_duration` zones sum to about `duration`.
- APK facts:
  - `SportBasicReportKt.isValid` rejects `start_time ≥ end_time`, `start_time ≤ 0`, and `duration ≤ 0` or `> 2678400` (31 days in seconds).
  - The Health Connect export (`SportSyncBiz`) uses `Instant.ofEpochSecond(start_time/end_time)`, `Length.meters(distance)` (skipped when `distance ≤ 0`), and a zone offset from `timezone` (`getTzOffsetInSecBy15min`).

**Window and paging**
- Window `[S−1, S]` returned the workout starting at `S`; window `[S, S+1]` returned nothing. So the service selects `start_time < time <= end_time` on the outer `time` (the workout's start), the same rule as daily reports (notes/06). It is not an overlap test and not an end-time test.
- With `limit=2`, the first page had `has_more=true` and a `next_key`. The second page, sent with that `next_key`, continued with no overlap and no gap against a one-page read.
- `category:"running"` returned only the running workout.

**Sport types on the account (ids only):** `{1, 6, 300, 308}`.
- APK `FitnessSportType` names: `RUNNING_OUTDOOR=1`, `BIKING_OUTDOOR=6`, `CLIMBING_MACHINE=300`, `STRENGTH_TRAINING=308`.
- The app's Health Connect mapping (`TypeConvert.convertMiToHealthConnectType`): 1→56 RUNNING, 6→8 BIKING, 300→69 STAIR_CLIMBING_MACHINE, 308→70 STRENGTH_TRAINING.

## Global Constraints

- **Read-only and low frequency.** Tasks 1–2 make no live calls. Task 3 runs one export (a page or two). Stop at the first 401.
- **No secrets or health data in the repo.** Fixtures are synthetic. Never print, log or commit a token, `sid`, date or health value from the account. Scratch work stays in `work/m4/` (git-ignored).
- **Tests stay offline:** a fake transport only. `python -m pytest -q` is green after every task. The baseline is what it reports before Task 1 (354 passed when this plan was written).
- **A + B.**
  - `DomainResult.raw` keeps every decoded `value` dict untouched.
  - B uses neutral names, and no Mi field name appears inside `models/`.
  - The Mi number → neutral name table lives only in `domains/workouts.py`.
- **Anti-goal (design §2): a field whose meaning is not verified stays out of B.** These stay `raw`-only: `min_hrm`, `total_cal`, every `hrm_*_duration` zone, pace / speed / cadence / stride / height fields, training effect / load / VO2max / recovery, `proto_type`, `version`, value `time`, and everything else not listed in `Workout`.
- **Units:** seconds, metres, kcal, bpm. Times are true Unix seconds. `utc_offset_s = timezone × 900`.
- **"Not reported" is `None`:** every figure that is absent, `0`, negative, a `bool` or not a number becomes `None` (`as_int`, then `> 0`). The exception is `utc_offset_s`, where `0` (UTC) is kept.
- **Selection:** a workout is in the result when its outer `time` satisfies `start_epoch_s <= time <= end_epoch_s`.
- **Backward compatible:** no existing domain, facade method, model or `_FULL_DOMAINS` changes.
- **No code is ported from Mi-Fitness-Sync,** so there is no new attribution header. `tests/test_licensing.py` and `THIRD_PARTY_NOTICES` stay unchanged.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Ask the user before any push.

## Review Focus

- **A workout starting exactly on a bound** (the first day's 00:00:00, or the last day's 23:59:59 local time) is included. One starting a second outside is excluded, without a note. → Task 1 `test_requests_from_a_second_before_start`, `test_rows_on_both_bounds_are_kept`, `test_rows_outside_the_window_are_dropped`.
- **A sport type the table does not know** (say pool swimming, `9`): the workout is kept with `sport=None` and its source number, with no note and no crash. → Task 1 `test_unmapped_sport_type_keeps_the_source_code`.
- **A workout without distance or steps** (strength training): `distance_m` and `steps` are `None`, not `0`. → Task 1 `test_maps_core_fields` (the strength row) and `test_absent_or_zero_figures_become_none`.
- **A long range that reaches the page cap** must say so in `notes`. A read that ends naturally must not. → Task 1 `test_capped_read_says_so`, `test_uncapped_read_has_no_cap_note`.
- **A workout deleted in the app** (a row with `deleted: true`) appears in neither `records` nor `raw`. → Task 1 `test_deleted_rows_are_dropped`.

---

### Task 1: `Workout` model + `workouts` domain + notes/10

**Files:**
- Create: `src/mifit/models/workouts.py`, `src/mifit/domains/workouts.py`, `tests/test_domain_workouts.py`, `tests/fixtures/workouts_sample.json`, `notes/10-workout-records.md`

**Interfaces:**
- Consumes:
  - `Transport.get_paged_result(endpoint_path, data_obj, *, list_key="data_list", sign_path=None, max_pages=None) -> PagedResult(items, truncated)`
  - `DomainResult`, `register`, `as_int` from `domains/base.py`
- Produces:
  - `models/workouts.py`:

    ```python
    @dataclass
    class Workout:
        start_epoch_s: int
        end_epoch_s: int | None
        utc_offset_s: int | None
        sport: str | None
        source_sport_code: int | None
        duration_s: int | None
        distance_m: int | None
        active_kcal: int | None
        avg_hr_bpm: int | None
        max_hr_bpm: int | None
        steps: int | None
    ```

    The docstring says:
    - start and end are true Unix seconds; `utc_offset_s` is the offset of the zone the workout was recorded in, for showing local clock times;
    - `sport` is a neutral name in Health Connect's exercise-type vocabulary, lower snake case, or `None` when the source type has no mapping yet;
    - `source_sport_code` is the source's own sport-type number, kept uninterpreted (like `HeartRatePoint.kind`);
    - `duration_s` is the recorded workout time and can be shorter than end − start, because paused time is not counted;
    - `active_kcal` is the energy burned by the activity, without resting energy;
    - any figure is `None` when the source did not report it, never a made-up zero.
  - `domains/workouts.py`: `@register("workouts") def fetch_workouts(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult`. It defines:
    - `_ENDPOINT = "data/get_sport_records_by_time"`
    - `_MAX_PAGES = 20`
    - `_TZ_UNIT_S = 900`
    - `_SPORTS = {1: "running", 6: "biking", 300: "stair_climbing_machine", 308: "strength_training"}`
- Request: `t.get_paged_result(_ENDPOINT, {"start_time": start_epoch_s - 1, "end_time": end_epoch_s, "reverse": True, "limit": 50}, list_key="sport_records", max_pages=_MAX_PAGES)`.
- Row rules, applied in this order; records keep service order (newest first):
  1. An outer `time` that is missing or unusable (`as_int` is `None`) → skip, with the note `"Skipped a workout row without a usable time."`. Not added to `raw`.
  2. An outer `time` outside `[start_epoch_s, end_epoch_s]` → skip silently.
  3. A truthy `deleted` → skip silently.
  4. A `value` that is not valid JSON → skip, with the note `"Skipped a workout row whose value is missing or not JSON."`. A `value` that is not an object → skip, with the note `"Skipped a workout row whose value is not an object."`. Neither goes into `raw`.
  5. Append the decoded value to `raw`.
  6. `start_epoch_s` is the positive `start_time` from the value. If there is none, the record is skipped with the note `"Skipped a workout without a usable start time."` (the value stays in `raw`).
- Field map (wire → B):
  - `end_time → end_epoch_s`
  - `timezone × 900 → utc_offset_s`, via `as_int`, so `0` is kept
  - `_SPORTS.get(code) → sport`, where `code = as_int(sport_type)`
  - `code → source_sport_code`
  - `duration → duration_s`
  - `distance → distance_m`
  - `calories → active_kcal`
  - `avg_hrm → avg_hr_bpm`
  - `max_hrm → max_hr_bpm`
  - `steps → steps`

  Every figure except `utc_offset_s` and `source_sport_code` goes through a local `_positive(raw) -> int | None` (`as_int`, then `None` unless `> 0`).
- Cap: when `truncated` is true, append `f"Workout list stopped at its {_MAX_PAGES}-page cap while the service had more: the oldest workouts in the range are missing; ask for a shorter range."`

- [ ] **Step 1: Write the fixture and the failing tests.**
  - **Fixture:** `{"code": 0, "message": "ok", "result": {"sport_records": [STRENGTH, RUN], "has_more": false, "next_key": ""}}`. Each row is the outer shape from "Live-verified shapes", with its value JSON-encoded. With `DAY_1 = 1760400000`:
    - `RUN`: `time = start_time = DAY_1 + 36000`, `end_time = start + 3001`, `duration 2700`, `timezone 32`, `sport_type 1`, `proto_type 22`, `distance 6000`, `calories 400`, `total_cal 470`, `steps 7000`, `avg_hrm 150`, `max_hrm 175`, `min_hrm 95`, `avg_pace 450`, `hrm_aerobic_duration 1500`, `version 6`. Outer `key "outdoor_running"`, `category "running"`.
    - `STRENGTH`: `time = start_time = DAY_1 + 151200`, `end_time = start + 1201`, `duration 1200`, `timezone 32`, `sport_type 308`, `proto_type 8`, `calories 150`, `total_cal 180`, `avg_hrm 110`, `max_hrm 140`, `min_hrm 80`, `version 6`. No `distance` and no `steps`.
  - **Helpers:** `START = DAY_1`, `END = DAY_1 + 172799`. `FakeTransport` is a copy of `test_domain_heart_rate.FakeTransport`; its `get_paged_result` records `{endpoint_path, data_obj, list_key, max_pages}`. Add helpers `row(time, value, **outer)` (with `value` a dict, a string or `None`) and `payload_with_rows(*rows)`.
  - **Tests:**
    - `test_workouts_domain_is_registered`: `REGISTRY["workouts"] is fetch_workouts`.
    - `test_requests_from_a_second_before_start`: exactly one call, with `endpoint_path == "data/get_sport_records_by_time"`, `data_obj == {"start_time": START - 1, "end_time": END, "reverse": True, "limit": 50}`, `list_key == "sport_records"` and `max_pages == 20`.
    - `test_maps_core_fields`:
      - `records == [Workout(DAY_1+151200, DAY_1+152401, 28800, "strength_training", 308, 1200, None, 150, 110, 140, None), Workout(DAY_1+36000, DAY_1+39001, 28800, "running", 1, 2700, 6000, 400, 150, 175, 7000)]`, using keyword arguments in the real test;
      - `notes == []`.
    - `test_maps_the_known_sport_types`: codes `1`, `6`, `300` and `308` give `"running"`, `"biking"`, `"stair_climbing_machine"` and `"strength_training"`.
    - `test_unmapped_sport_type_keeps_the_source_code`:
      - code `9` gives `sport is None` and `source_sport_code == 9`, and the record is still built;
      - a missing code, `"1"` or `true` gives both as `None`;
      - `notes == []`.
    - `test_absent_or_zero_figures_become_none`: `distance` absent, `steps: 0`, `calories: 0`, `avg_hrm: 0`, `max_hrm: -1` and `duration: 0` each give `None` for that field only.
    - `test_non_numeric_figures_become_none`: `"72"`, `true`, `[1]` and `NaN` (written into the JSON as `NaN`) give `None` with no exception.
    - `test_utc_offset_comes_from_quarter_hours`: `timezone` `32 → 28800`, `-20 → -18000`, `0 → 0`, and absent → `None`. The start instant is the same in every case.
    - `test_rows_on_both_bounds_are_kept`: outer `time` (and `start_time`) `START` and `END` give two records.
    - `test_rows_outside_the_window_are_dropped`: outer times `START - 1` and `END + 1` appear in neither `records` nor `raw`, and `notes == []`.
    - `test_deleted_rows_are_dropped`: a row with `deleted: true` appears in neither `records` nor `raw`, and `notes == []`.
    - `test_row_without_usable_time_is_skipped_with_note`: an outer `time` that is absent, `"x"` or `true` gives no record, no `raw` entry, and one note containing `"usable time"` per row.
    - `test_row_with_bad_value_is_skipped_with_note`: values `"not json"`, `"[1]"` and a missing `value` give one note each, and none of them appears in `raw`.
    - `test_workout_without_start_is_skipped_with_note`: a value with no `start_time`, or `start_time: 0`, gives no record and one note containing `"start time"`; its value is still in `raw`.
    - `test_raw_keeps_decoded_values_untouched`: `result.raw` equals the two decoded fixture values in service order, with `total_cal`, `min_hrm`, `proto_type` and `hrm_aerobic_duration` included.
    - `test_capped_read_says_so`: with `truncated=True`, exactly one note, which contains `"20-page cap"`.
    - `test_uncapped_read_has_no_cap_note`: on the fixture, `notes == []`.
    - `test_unverified_fields_stay_out_of_workout`: `{f.name for f in dataclasses.fields(Workout)}` equals the 11 names above.
    - `test_empty_window_yields_no_records_and_no_notes`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_workouts.py -v`. Expected: FAIL, because the module is missing.
- [ ] **Step 3: Implement** `Workout` and `fetch_workouts` by the rules above. The module docstring says:
  - the endpoint and its exclusive start;
  - selection is by start instant, with the request starting one second early and filtered on the outer `time`;
  - records come newest first, deleted rows are ignored, and the page cap is noted;
  - `_SPORTS` covers only verified types and is the place to add more (one entry plus one test line each);
  - which fields stay `raw`-only and why: meaning unverified, design §2;
  - a pointer to notes/10.
- [ ] **Step 4: Write `notes/10-workout-records.md`** (Chinese, like notes/01–08), using only synthetic values and no account dates, counts or `sid`s:
  - §1 接口与请求: the APK request class and its wire names; what the app sends by default; that upstream's `startTime`/`endTime` are wrong.
  - §2 返回形状与字段: the outer row; the value's present / optional keys; units and the verified relations from "Live-verified shapes"; the APK `isValid` bounds.
  - §3 时间窗、翻页、category: the `[S−1,S]` / `[S,S+1]` experiment and the rule; `limit=2` paging; the `category` filter, which the SDK does not use yet.
  - §4 运动类型:
    - the four ids on the account and their neutral names;
    - the source of truth for further ids: APK `com.xiaomi.fit.data.common.data.annotation.FitnessSportType` (names and ids) and `com.xiaomi.fitness.repo.healthconnect.biz.sport.TypeConvert` (the app's Health Connect mapping);
    - how to add a type: one entry in `_SPORTS`, one line in `test_maps_the_known_sport_types`, and a check against the app.
  - §5 B/A 划分与未决:
    - `calories` vs `total_cal`: which one the app shows is checked in Task 3;
    - `duration` vs end − start: pauses;
    - the zone durations: they sum to about `duration`, but their thresholds are user-specific, so they stay A-only;
    - `deleted` and `cheat_info` were never seen live;
    - FDS detail is M6.
- [ ] **Step 5: Run** the file and then `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 6: Commit** `git add src/mifit/models/workouts.py src/mifit/domains/workouts.py tests/test_domain_workouts.py tests/fixtures/workouts_sample.json notes/10-workout-records.md && git commit -m "feat(domains): sport records -> Workout (workouts domain)"`

---

### Task 2: Facade, CLI and package export

**Files:**
- Modify: `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (only the domain list in the module docstring), `tests/test_client.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `fetch_workouts` and `Workout` from Task 1.
- Produces:
  - `MiFit.workouts(self, start: DateArg, end: DateArg) -> DomainResult`, returning `fetch_workouts(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Docstring: one record per workout that started between the start of `start` and the end of `end` (local days), newest first. Each record gives the sport, start and end, duration, distance, active calories, average and maximum heart rate and steps; the full source report for each workout is in `raw`.
  - `mifit.Workout`, exported in `__all__`.
  - `mifit export workouts`. The CLI dispatches through `REGISTRY` and `getattr`, so this needs no CLI code. `--full` triggers the existing "ignored" note.

- [ ] **Step 1: Write failing tests:**
  - `test_client.py`:
    - `test_workouts_delegates_to_domain_with_local_day_bounds`: a spy on `client_module.fetch_workouts`, exactly like `test_heart_rate_daily_delegates_to_domain_with_local_day_bounds`.
    - `test_importing_client_registers_the_domains`: the expected value becomes `"['consumption', 'heart_rate', 'heart_rate_daily', 'sleep', 'steps', 'workouts']"`.
    - `test_workouts_is_registered_once_client_is_imported`.
    - `test_package_exports`: add `mifit.Workout is Workout`, and add `"Workout"` to the `__all__` loop.
  - `test_cli.py`:
    - Add `WORKOUT = Workout(start_epoch_s=1790820000, end_epoch_s=1790822701, utc_offset_s=28800, sport="running", source_sport_code=1, duration_s=2400, distance_m=5000, active_kcal=350, avg_hr_bpm=148, max_hr_bpm=170, steps=6000)`.
    - Give `FakeMiFit.workouts(start, end)` a return of `DomainResult(records=[WORKOUT], raw=[{"sport_type": 1}], notes=[])`, recording the call like `heart_rate_daily`.
    - `test_export_workouts_writes_records`: `records == [asdict(WORKOUT)]`, and the calls are `[("workouts", "2026-10-01", "2026-10-02", {})]`.
    - `test_export_full_flag_is_ignored_for_workouts`: exit code 0, `"--full"` appears in stderr, and the call carries no `full`.
- [ ] **Step 2: Run** `python -m pytest tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement:**
  - the facade method;
  - the domain import in `client.py` (that import is what registers the domain);
  - the package export;
  - `workouts` in the CLI docstring's domain list.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. Then `python -m pip install -e . && mifit export --help` should list `workouts`.
- [ ] **Step 5: Commit** `git commit -m "feat(client,cli): expose workouts"`. The README stays unchanged until Task 3, so it never claims an unverified domain.

---

### Task 3: Live verification (manual, needs the user; deferred by the controller to the end-of-run session) + roadmap + README

Tasks 1–2 do not depend on this task. The controller runs it later, with the user present.

**Files:**
- Create: `docs/superpowers/verification/<run date>-workouts-live.md`
- Modify:
  - `docs/superpowers/roadmap.md`: §0 M4 status, §1 domain list, §3 M4 `结果` line. The roadmap may have moved on, so edit only the M4 lines.
  - `README.md`:
    - the `workouts` row in "What it reads today";
    - `workouts` in the export `<domain>` list;
    - one Known-limitations line.
  - Only if Step 3 says so: `src/mifit/models/workouts.py`, `src/mifit/domains/workouts.py`, `tests/test_domain_workouts.py`, `tests/test_cli.py`, `notes/10-workout-records.md`.

- [ ] **Step 1:** Run, writing outside the repo: `mifit export workouts --from <a day before the user's 3–4 most recent workouts> --to <today> --json "$HOME\mifit-verify\workouts.json"`.
- [ ] **Step 2:** The user opens the app's workout records (运动记录) for the same range and reads:
  - how many workouts there are;
  - for each one: the sport name, the start clock time, the duration, the distance (if shown), the calories, the average and maximum heart rate, and the steps (if shown).
- [ ] **Step 3:** Compare, per workout (rounding as the app displays):
  - The count matches.
  - `sport` corresponds to the app's sport name.
  - `start_epoch_s + utc_offset_s` read as UTC gives the app's local start time.
  - `duration_s` matches the app's duration.
  - `distance_m / 1000` matches the app's km.
  - `active_kcal` matches the app's calories.
  - `avg_hr_bpm` / `max_hr_bpm` and `steps` match.
  - Mismatches and decisions:
    - If the app's calories equal `total_cal` instead: rename the field to `total_kcal`, source it from `total_cal`, and update the docstring, `test_maps_core_fields`, the field-set test and the CLI `WORKOUT`.
    - If the app's duration equals end − start: change the docstring only (the field stays `duration`). Record which way it went.
    - Any other field that does not match leaves B (it stays in `raw`). Record the decision.
- [ ] **Step 4:** Record pass / partial per field in the verification doc: match or differ only, and no values beyond what a row needs. Then:
  - Update the roadmap: M4 ✅ with a 结果 line, and the domain list in §1.
  - Update the README:
    - row: `` `workouts` | one row per workout: sport, start/end, duration, distance, active calories, average/max heart rate, steps | `Workout` ``;
    - export list: add `workouts`;
    - Known limitations: "Workout detail (per-second samples, GPS track) is not read yet; only the summary list is."
  - Run `python -m pytest -q`, then commit `docs: workouts live verification; roadmap M4`. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Names.** The domain is `workouts`. `fetch_workouts` lives in `domains/workouts.py` and `Workout` in a new `models/workouts.py`. The facade method is `MiFit.workouts(start, end)` and the CLI command `mifit export workouts`. The neutral field names follow design §12.1 (`duration_s`, `distance_m`) and the existing models (`active_kcal` as in `ConsumptionRecord`; `avg_hr_bpm` / `max_hr_bpm` as in `SleepRecord`). The neutral sport field is `sport`, not `sport_type`, because `sport_type` is the Mi wire name.
2. **Selection by start instant, not by local day.**
   - Rows are instants keyed by their start, not daily-report keys, so `days.py` is not involved.
   - The facade's `to_epoch_s` already turns the caller's dates into machine-local day bounds.
   - The request starts at `start_epoch_s − 1`, because the service excludes `start_time` (verified live).
   - The client-side filter on the outer `time` makes the result exact even if that bound rule changes.
   - The record's own zone is used only for display (`utc_offset_s`), never for selection.
3. **B fields are exactly the roadmap's core list:** sport, start/end, duration, distance, calories, average/maximum heart rate and steps. Two neutral additions are needed to read them correctly: `utc_offset_s` and `source_sport_code`.
   - `min_hrm` stays `raw`-only, because the roadmap does not list it.
   - `total_cal` stays `raw`-only until Task 3 shows which calorie figure the app displays. B carries `calories` as `active_kcal`, because the live relation makes it the active energy.
4. **`duration_s` is `duration` (moving time),** not end − start. Pace and speed agree with it, and the APK's validity check uses it. Task 3 confirms that the app shows it.
5. **The sport table covers only the account's four types** (roadmap: "先覆盖你实际做过的类型").
   - The names come from Health Connect's exercise-type vocabulary, matching the app's own Health Connect mapping (design §12.1).
   - Unknown types are kept, with `sport=None` and their source number, and get no note: an unmapped type is expected, not an error.
   - notes/10 §4 names the APK classes that hold the full table.
6. **`utc_offset_s` comes from the value's `timezone × 900`.** The APK uses `tzIn15Min` for Health Connect zone offsets, and it equaled the outer `zone_offset` on every live row. `zone_name` stays out.
7. **`0` → `None` for every figure,** as in heart-rate daily and sleep. The app itself drops `distance ≤ 0` and treats `duration ≤ 0` as invalid. Non-distance sports simply omit the key.
8. **Page cap: 20 pages × 50 = 1000 workouts per call,** reported through `get_paged_result` with a note. Chunking and resuming long ranges belong to T3. M4 does not wait for T2: the read is a single short call.
9. **No `category` or sport filter parameter (YAGNI).** Callers filter `records`. The server-side `category` filter is verified and documented in notes/10 §3, ready if T3 needs it.
10. **Rows marked `deleted` are ignored silently,** as sleep does with deleted raw rows. They are what the app hides.
11. **`raw` holds the decoded `value` dicts only,** as in every other domain. The outer row (`sid`, `key`, `category`, `zone_name`, `watermark`) is not exposed. M6 (FDS, which needs `sid`) will add that access deliberately.
12. **Records come in service order, newest first** (`reverse=true`, as the app requests).
13. **Nothing is ported from Mi-Fitness-Sync's activity client,** so no attribution header or `THIRD_PARTY_NOTICES` entry is needed. Two of its claims are wrong:
    - its request uses `startTime`/`endTime`, but the APK's `@SerializedName`s are `start_time`/`end_time`;
    - its docs name the zone fields `hr_*_duration`, but the wire names are `hrm_*_duration`.

    The domain follows this repo's own domain pattern instead.
14. **notes/10 is written in Task 1,** so the module docstring's pointer is valid from the first commit.
15. **The README lists the domain only after Task 3 verifies it,** as in M3 (the T1 rule "do not over-claim verification").
16. **Task 3 is a manual, user-attended task** that the controller defers to the end-of-run session. Tasks 1–2 are complete and releasable without it, but unlisted in the README.

## Self-Review

1. **Spec coverage**, against roadmap M4:
   - The endpoint with `reverse=true`, `limit=50` and `next_key` paging is Task 1's request through `get_paged_result`.
   - The optional `category` is verified and documented but not exposed (decision 9).
   - The A layer is a full passthrough of each `SportBasicReport` (`raw`, `test_raw_keeps_decoded_values_untouched`).
   - B `Workout` holds only the core fields (`test_unverified_fields_stay_out_of_workout`; decisions 3–4).
   - The sport-type mapping sits in the mapping layer, covers the user's types and keeps the raw id for others (`_SPORTS`, decision 5).
   - The completion criterion (count and core fields match the app) is Task 3. ✓
2. **Step scan:** every code step names its file, signature and pinned values. The only conditional, in Task 3 Step 3, spells out the change for each outcome. ✓
3. **Type consistency:** `Workout`'s 11 fields, `fetch_workouts(t, start_epoch_s, end_epoch_s)`, `_MAX_PAGES = 20`, `list_key="sport_records"` and the `"20-page cap"` note are named the same in every task. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1. ✓
5. **Proportion:** three tasks for one domain. The only code block is the dataclass signature. ✓
