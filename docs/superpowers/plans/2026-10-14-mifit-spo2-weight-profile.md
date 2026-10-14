# mifit SpO2 + Weight + Profile Implementation Plan (M5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four read-only reads: `spo2_daily` (one neutral `Spo2DailySummary` per local day: average, highest and lowest SpO2 % from the service's daily report), `spo2` (raw all-day SpO2 points, complete at the window start), `weight` (weight records: time, kg, BMI), and `MiFit.profile()` (height, weight and the step and calorie goals from the account profile).

**Architecture:** `spo2_daily` is a close copy of `heart_rate_daily` (`get_aggregated_fitness_data_by_time`, `tag=daily_report`, `key=spo2`, steps' day rules). Raw SpO2 has the same first-bucket drop as raw heart rate, so the "ask a day earlier, then filter" fix moves out of `heart_rate.py` into one shared helper, `domains/raw_window.py`; `heart_rate`, `spo2` and `weight` all read through it. The profile lives on a different base path (`healthapp/` instead of `app/v1/`) and takes no `data` parameter, so `Transport.get_json` gains a `base_path` keyword and accepts `data_obj=None`; the profile is not a time series, so it is a facade method and a CLI subcommand, not a registered domain.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport`, `domains.days`, `DomainResult`, `register`, `as_int`), pytest. No new deps.

**Spec:** roadmap `docs/superpowers/roadmap.md` (§3 M5, §6 "M5 告知体重/体脂秤数据情况", §7); design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` (§2 anti-goals, §3 risk control, §6 data-domain list incl. `weight_item_record` / `user_info_profile`, §8.1, §9 open item "`user_info_profile`/目标…的确切云端入口", §12); evidence `notes/04-mi-health-lab-cloud-breakthrough.md` §2/§4, `notes/06-daily-report-day-semantics.md`, `notes/08-heart-rate-keys-and-completeness.md` (bucket rule); the live findings below.

**Precondition:** T2 (session renewal) is merged into `main`, so `Transport.get_json` sends through `Transport._send(endpoint_path, data_obj, sign_path)`, and every request retries once after a renewal. M4 may or may not be merged; nothing here depends on it. `notes/09` and `notes/10` are taken, so this milestone's notes file is `notes/11`. Work goes onto a short branch that is fast-forwarded into `main`.

## Live-verified shapes (UTC+8 account, 2026-10-14, 10 read-only calls ≥ 2 s apart, no 401; all values below are synthetic)

**APK (3.59.1, dex constants and `@SerializedName`s)**
- `FitnessPersistKey`: `Spo2 = "spo2"`, `ManualSpo2 = "single_spo2"`, `AbnormalSpo2 = "low_spo2"`, `Weight = "weight"` (also `ReportMaxSpo2 = "max_spo2"`, `ReportMinSpo2 = "min_spo2"`, not probed).
- `CloudSpo2Report`: `avg_spo2`, `max_spo2`, `min_spo2`, `lack_spo2_count` (ints), `latest_spo2` (`Spo2Item{spo2, time}`), `spo2_total`, `spo2_time_total` (nullable ints). `CloudDailyHelper.convertSpo2` builds it from `DailySpo2Report` (max/min/avg, `lackTimes`, `latestSpoRecord`; the two totals are always `null`) and uploads only when `maxSpo2 > 0`.
- Weight is pulled with key `"weight"`: `CrossYearSyncController.pullOneYearWeightIfNeed` → `pullAndSaveFitnessDataByTime(…, "weight", start, end, …)`. Value class `WeightItem` (≈60 fields: weight, bmi, body-fat/muscle/moisture/protein rates, segmental fat/muscle, body score, …).
- Profile: `AccountService.getUserProfile()` (no parameters) → `BaseResult<UserInfoProfile>`, with `HOST = "https://hlth.io.mi.com/"`, `PATH = "healthapp/"`; the path strings are `user/get_miot_user_profile`, `user/set_miot_user_profile`, `user/remove_miot_user_profile` (the last two write and are never called).

**Daily report `spo2`** (`CloudSpo2Report`)
- Outer row: the same as every daily report, `{tag:"daily_report", key:"spo2", time:<UTC midnight of the local date>, value:<JSON string>, update_time, watermark, source_sid_list, sid}`. Rows exist only for days with SpO2 data (a few recent days on this account).
- Decoded value (every key present on every row seen): `{"avg_spo2": 96, "max_spo2": 99, "min_spo2": 92, "lack_spo2_count": 0, "latest_spo2": {"spo2": 97, "time": 1760486400, "dbTime": 1760457600, "dbKey": "spo2", "sid": "synthetic"}}`. `spo2_total` / `spo2_time_total` never appeared.
- On every row `min ≤ avg ≤ max`, all within 70–100. `lack_spo2_count` was always `0`.
- `latest_spo2.time − dbTime = +28800`: `time` is the shifted local clock, as in `latest_hr` (notes/08 §1.1).

**Raw `spo2`** (`get_fitness_data_by_time`)
- Outer `{key, sid, time, update_time, value, zone_name, zone_offset}`; outer `time` = `value.time` on every row (a true instant); `zone_offset` is an int in seconds, `zone_name` an IANA zone. Value `{"time": 1760400600, "spo2": 97}`.
- Cadence about 10 min while monitoring is on; older history is a few scattered points. One read without `start_time` returned one page of a few dozen rows reaching back months, with `has_more=true`: the page size is the service's choice.
- **First-bucket drop: present, exactly as for heart rate.** Window `[H, end of that local day]`, H = local 16:00 (an even whole UTC hour on UTC+8):
  - the no-start read has 36 rows in the window;
  - the window read returned 27; the 9 missing rows are exactly those in `[H, H+2h)` (first row returned at H+2h02m);
  - the same window with `start_time = H − 86400` returned all 36, identical in `time` and `value` to the no-start rows; no row was added or changed.

**Raw `single_spo2`**: value `{"time", "spo2"}`, outer `time` = `value.time`; pages of 20 rows with `has_more`; scattered spot measurements. **Raw `low_spo2`**: no rows on this account.

**Raw `weight`**
- One row on the account, several years old. A no-start read and a 6-year window read (`start_time` far before it) both return exactly that row, `has_more=false`.
- Outer `{key, sid, time, update_time, value, zone_name, zone_offset}`; outer `time` = `value.time`, not a midnight.
- Value (types as live): `{"time": 1600000000, "weight": 70.2, "bmi": 22.9, "body_fat_rate": 0.0, "muscle_rate": 0.0, "moisture_rate": 0.0, "bone_mass": 0.0, "protein_rate": 0.0, "visceral_fat": 0.0, "basal_metabolism": 0}`.
- `weight` is kg: it equals the profile's `weight` after rounding. `bmi` equals `weight / (profile height in m)²` to 0.1. Every body-composition field is `0` (as notes/04 §4 saw), so none of them can be checked.

**Profile** `GET https://hlth.io.mi.com/healthapp/user/get_miot_user_profile`
- Request: no `data` parameter (the query is only `rc4_hash__`, `signature`, `_nonce`), signature path `/user/get_miot_user_profile` (the `healthapp/` prefix is stripped and the leading `/` kept, the same rule as FDS `gen_download_url`, notes/04 §2d). The response is encrypted like `app/v1/` and is `{code: 0, message, result}`. The same path under `app/v1/` fails (non-401 error).
- `result` (synthetic): `{"id": "x1", "xiaomi_id": 1000001, "name": "synthetic", "sex": "male", "birth": "1990-01-01", "age": 35, "height": 175, "weight": 70, "initial_weight": {"weight": 70, "timestamp": 1600000000}, "daily_step_goal": 8000, "daily_cal_goal": 300, "daily_sleep_goal": 480, "stand_goal": 0, "regular_goal_list": [{"field": 1, "target": 8000}, {"field": 2, "target": 300}, {"field": 4, "target": 30}], "record_max_hrm": {"hrm": 190, "source": "auto"}, "region": "CN", "personalization": null, "wear_medal": null}`.
- `height` and `weight` came as ints although the APK declares floats; height is in the cm range. `daily_step_goal` and `daily_cal_goal` equal `regular_goal_list` fields 1 and 2; field 4's target matches no named goal. `daily_sleep_goal` is a multiple of 30 (likely minutes, unverified). `stand_goal` was `0`.
- `result` carries personal identifiers (`name`, `birth`, `id`, `xiaomi_id`, `region`).

## Global Constraints

- **Read-only and low frequency.** No live call in Tasks 1–4. Never call `user/set_miot_user_profile`, `user/remove_miot_user_profile` or any POST.
- **No secrets, identifiers or health/profile data in the repo.** Never print, log or commit a token, an account id, a name, a birth date, a height, a weight or a health value; tests and notes use synthetic values only.
- **Tests stay offline:** synthetic fixtures and fake transports only. `python -m pytest -q` is green after every task. The baseline is what it reports before Task 1 (about 441 passed when this plan was written, with T2 merged).
- **A + B.** `DomainResult.raw` keeps every decoded `value` dict (for the profile: the `result` dict) untouched. B models use neutral names; no Mi field name or Mi code appears inside `models/`.
- **Anti-goal (design §2): a field whose meaning is not verified stays out of B.** These stay A-only: `lack_spo2_count`, `latest_spo2`, `spo2_total`, `spo2_time_total`; every weight body-composition field; the profile's sleep and stand goals, `regular_goal_list`, `initial_weight`, `record_max_hrm`, `age`, `sex`, `birth`, `name`, `id`, `xiaomi_id`, `region`; raw `single_spo2` / `low_spo2` (read them with `MiFit.raw`).
- **The daily SpO2 average is the service's `avg_spo2`.** The app's daily average is not the plain mean of the points (notes/04 §4); never recompute or fit it.
- **Days for `spo2_daily` are exactly steps' days:** window `report_window(*local_day_span(start, end, tz))`, `date = report_day(row.time)`, `epoch_s = local_midnight(date, tz)`.
- A figure that is absent, `0`, negative, a `bool` or not a number becomes `None`; nothing is fabricated.
- **Backward compatible:** `heart_rate` (names, request, notes, tests) is unchanged by the refactor; `Transport.get_json(endpoint_path, data_obj)` with a dict and no new keyword sends exactly what it sends today; `get_paged` / `get_paged_result` are untouched; `MiFit.raw` stays unfiltered.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Ask the user before any push.

## Review Focus

- **A raw SpO2 window whose first 2 h hold data** (it starts on a whole hour): every point must be there, and margin rows must not leak. → Task 2 `test_spo2_window_asks_a_day_before_start`, `test_spo2_window_keeps_rows_at_both_bounds`, `test_spo2_window_drops_rows_outside_the_asked_window`.
- **Heart rate after the shared-helper refactor:** request, filtering and notes must be byte-for-byte as before. → Task 2 Step 4 runs `tests/test_domain_heart_rate.py` unchanged; `test_raw_window_counts_rows_without_usable_time` pins the helper's counting.
- **A daily SpO2 row with an absent, `0`, string or bool figure:** that field is `None`, the others keep their values, no crash, no note. → Task 1 `test_missing_or_zero_figures_become_none`, `test_non_numeric_figures_become_none`.
- **A weight row whose `weight` is absent or `0`:** no 0-kg record (skipped with a note), and the all-zero body composition never surfaces in B. → Task 3 `test_row_without_usable_weight_is_skipped_with_note`, `test_composition_stays_out_of_the_record`.
- **The profile request and a non-cn account:** it must go to `healthapp/` (never `app/v1/`), without `data`, signed over `/user/get_miot_user_profile`, with the regional host prefix; an answer without a `result` object gives a note, not a crash. → Task 4 `test_base_path_replaces_app_v1_and_is_regionalized`, `test_none_data_sends_no_data_param`, `test_profile_requests_healthapp_without_data`, `test_answer_without_profile_object_gives_a_note`.

---

### Task 1: `Spo2DailySummary` + `spo2_daily` domain, exposed

**Files:**
- Create: `src/mifit/models/spo2.py`, `src/mifit/domains/spo2_daily.py`, `tests/test_domain_spo2_daily.py`, `tests/fixtures/spo2_daily_sample.json`
- Modify: `src/mifit/domains/base.py` (add `as_positive_int`), `tests/test_domain_base.py`, `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (module docstring domain list only), `tests/test_client.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Transport.get_paged(endpoint_path, data_obj, *, list_key="data_list", ...) -> list[dict]`; `days.local_day_span`, `days.report_window`, `days.report_day`, `days.local_midnight`; `DomainResult`, `register`, `as_int`.
- Produces:
  - `domains/base.py`: `def as_positive_int(raw: object) -> int | None` — `as_int(raw)` when it is `> 0`, else `None`. (Existing modules keep their own `_bpm` / `_measured`; they are not touched.)
  - `models/spo2.py`: `@dataclass class Spo2DailySummary(date: str, epoch_s: int, avg_spo2_pct: int | None, max_spo2_pct: int | None, min_spo2_pct: int | None)`. Docstring: `date` is the local date and `epoch_s` its local midnight; the three figures are blood-oxygen saturation in percent for that day as the service reports it (the average is the service's own, not a mean of the points); each is `None` when not reported.
  - `domains/spo2_daily.py`: `@register("spo2_daily") def fetch_spo2_daily(t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None) -> DomainResult`.
  - `MiFit.spo2_daily(self, start: DateArg, end: DateArg) -> DomainResult` → `fetch_spo2_daily(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Docstring: one record per local day with SpO2 data, giving the day's average, highest and lowest SpO2 % as the service's daily report states them.
  - `mifit.Spo2DailySummary` in `__all__`; `mifit export spo2_daily` (via `REGISTRY`, no CLI code).
- Request: `get_paged("data/get_aggregated_fitness_data_by_time", {"tag": "daily_report", "key": "spo2", "reverse": True, "limit": 50, "start_time": …, "end_time": …}, list_key="data_list")`.
- Field map (wire → B), each through `as_positive_int`: `avg_spo2→avg_spo2_pct`, `max_spo2→max_spo2_pct`, `min_spo2→min_spo2_pct`.
- Row handling is `heart_rate_daily`'s, with "daily SpO2" in the note texts: bad JSON / non-object value → note, not in `raw`; otherwise into `raw` first, then a row without a usable outer `time` → note; a row with every figure `None` still gives a record.

- [ ] **Step 1: Write the fixture and failing tests.** Fixture: the `hr_daily_sample.json` envelope with two synthetic rows. `DAY_1 = 1760400000` holds every key of the "Live-verified shapes" daily value; `DAY_2 = DAY_1 + 86400` has no `min_spo2`. Copy `test_domain_heart_rate_daily.py`'s `FakeTransport` and `payload_with_rows`. Tests:
  - `test_as_positive_int` (in `test_domain_base.py`, parametrized): `97→97`, `96.0→96`, `0→None`, `-1→None`, `True→None`, `"97"→None`, `None→None`, `float("nan")→None`.
  - `test_spo2_daily_domain_is_registered`: `REGISTRY["spo2_daily"] is fetch_spo2_daily`.
  - `test_requests_daily_report_spo2_for_the_window`: with `tz=UTC`, `START = DAY_1`, `END = DAY_2 + 86399`, the one call's `data_obj == {"tag": "daily_report", "key": "spo2", "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}` on the aggregated endpoint.
  - `test_maps_day_figures`: `records[0] == Spo2DailySummary(date="2025-10-14", epoch_s=DAY_1, avg_spo2_pct=96, max_spo2_pct=99, min_spo2_pct=92)`, `notes == []`.
  - `test_epoch_is_the_local_midnight_of_the_report_date`: with `tz=timezone(timedelta(hours=8))` and matching local bounds, `date == "2025-10-14"` and `epoch_s == DAY_1 - 28800`.
  - `test_missing_or_zero_figures_become_none`: `min_spo2` absent, `null` or `0`, and `max_spo2` `0`, give `None` for that field only; the others keep their wire values; `notes == []`.
  - `test_non_numeric_figures_become_none`: `"96"`, `true`, `[96]` all give `None`, no exception.
  - `test_raw_keeps_decoded_values_untouched`: `raw` equals both decoded fixture values, `latest_spo2` and `lack_spo2_count` included.
  - `test_row_with_bad_value_is_skipped_with_note`: `"not json"` and `"[1]"` → no record, one note each, not in `raw`.
  - `test_row_without_usable_day_is_skipped_with_note`: outer `time` missing or `"x"` → no record, one note, value still in `raw`.
  - `test_unverified_fields_stay_out_of_the_summary`: `{f.name for f in dataclasses.fields(Spo2DailySummary)} == {"date", "epoch_s", "avg_spo2_pct", "max_spo2_pct", "min_spo2_pct"}`.
  - `test_empty_window_yields_no_records_and_no_notes`.
  - `test_client.py`: `test_spo2_daily_delegates_to_domain_with_local_day_bounds` (spy on `client_module.fetch_spo2_daily`, like the `heart_rate_daily` one); `test_spo2_daily_is_registered_once_client_is_imported`; `test_importing_client_registers_the_domains` gains `'spo2_daily'` in sorted position; `test_package_exports` adds `mifit.Spo2DailySummary is Spo2DailySummary`.
  - `test_cli.py`: `SPO2_DAY = Spo2DailySummary(date="2026-10-01", epoch_s=1790812800, avg_spo2_pct=96, max_spo2_pct=99, min_spo2_pct=None)`; `FakeMiFit.spo2_daily(start, end)` returns `DomainResult(records=[SPO2_DAY], raw=[{"avg_spo2": 96}], notes=[])`; `test_export_spo2_daily_writes_records` (`records == [asdict(SPO2_DAY)]`, calls `[("spo2_daily", "2026-10-01", "2026-10-02", {})]`).
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_spo2_daily.py tests/test_domain_base.py tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL (module and names missing).
- [ ] **Step 3: Implement** `as_positive_int`, the model, `fetch_spo2_daily` (copy `heart_rate_daily.py`'s structure), the facade method, the domain import in `client.py` (it registers the domain), the package export, and `spo2 daily` in the CLI docstring's domain list. The module docstring says: steps' day rules (notes/06); `avg_spo2_pct` is the service's daily average, deliberately not recomputed from points (notes/04 §4); `lack_spo2_count` and `latest_spo2` stay in `raw` (meaning unverified; `latest_spo2.time` is a shifted local clock).
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. `python -m pip install -e . && mifit export --help` lists `spo2_daily`.
- [ ] **Step 5: Commit** `git add src/mifit/models/spo2.py src/mifit/domains/spo2_daily.py src/mifit/domains/base.py src/mifit/client.py src/mifit/__init__.py src/mifit/cli/__init__.py tests/test_domain_spo2_daily.py tests/fixtures/spo2_daily_sample.json tests/test_domain_base.py tests/test_client.py tests/test_cli.py && git commit -m "feat(domains): SpO2 daily report -> Spo2DailySummary (spo2_daily)"`

---

### Task 2: Shared raw-window read + `spo2` points domain + notes/11

**Files:**
- Create: `src/mifit/domains/raw_window.py`, `src/mifit/domains/spo2.py`, `tests/test_domain_raw_window.py`, `tests/test_domain_spo2.py`, `notes/11-spo2-weight-profile.md` (Chinese, like notes/01–10)
- Modify: `src/mifit/domains/heart_rate.py` (window read only), `src/mifit/models/spo2.py` (add `Spo2Point`), `src/mifit/client.py` (`spo2` method; `raw` docstring), `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (docstring list), `tests/test_client.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Spo2DailySummary` module from Task 1 (same file), `as_positive_int`, `as_int`, `DomainResult`, `register`.
- Produces:
  - `domains/raw_window.py`: `RAW_ENDPOINT = "data/get_fitness_data_by_time"`, `START_MARGIN_S = 86400`, and `def fetch_raw_window(t: Transport, key: str, start_epoch_s: int, end_epoch_s: int) -> tuple[list[dict], int]`. It sends one `t.get_paged(RAW_ENDPOINT, {"key": key, "start_time": start_epoch_s - START_MARGIN_S, "end_time": end_epoch_s, "reverse": True}, list_key="data_list")` and returns `(kept, unusable)`: `kept` is the entries (in service order, untouched) whose outer `time` (`as_int`) satisfies `start_epoch_s <= time <= end_epoch_s`; `unusable` counts entries that are not dicts or have no usable outer `time`. Margin rows are dropped silently. Module docstring: the bucket rule (notes/08 §2.3, notes/11 §1.3) and that every bucketed raw key must be read through here.
  - `heart_rate.fetch_heart_rate_window` calls `fetch_raw_window(t, "heart_rate", start, end)`, builds the result from `kept` with the unchanged `_build_result`, then appends `"Skipped a heart-rate row without a usable time."` once per unusable row. `_START_MARGIN_S` and the local loop are deleted; the docstring points to `raw_window`. `fetch_heart_rate_full` and `_ENDPOINT` stay.
  - `models/spo2.py`: `@dataclass class Spo2Point(epoch_s: int, spo2_pct: int)`. Docstring: one blood-oxygen sample; `epoch_s` is the sample time in Unix seconds, `spo2_pct` the saturation in percent as reported.
  - `domains/spo2.py`: `@register("spo2") def fetch_spo2_window(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult`. Per kept entry: value not JSON / not an object → note `"Skipped an SpO2 row whose value is missing or not valid JSON."` / `"…is not an object."`, not in `raw`; else into `raw`, then `Spo2Point(epoch_s=<outer time>, spo2_pct=as_positive_int(value.get("spo2")))`, and a row whose `spo2` is not a positive number is skipped with `"Skipped an SpO2 row without a usable SpO2 value."`. Then one `"Skipped an SpO2 row without a usable time."` per unusable row.
  - `MiFit.spo2(self, start: DateArg, end: DateArg) -> DomainResult` → `fetch_spo2_window(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Docstring: raw all-day SpO2 samples in the window, complete at its start (the read asks a day earlier and filters); spot checks (`single_spo2`) and low-SpO2 events (`low_spo2`) are separate keys, read them with `raw`.
  - `MiFit.raw` docstring: "such as `heart_rate`" becomes "such as `heart_rate` and `spo2` (notes/08, notes/11)".
  - `mifit.Spo2Point` in `__all__`; `mifit export spo2`.

- [ ] **Step 1: Write failing tests.**
  - `test_domain_raw_window.py` (a fake `Transport` whose `get_paged` records the call and returns given rows):
    - `test_raw_window_asks_a_day_before_start`: key `"weight"` → `data_obj == {"key": "weight", "start_time": START - 86400, "end_time": END, "reverse": True}`, endpoint `RAW_ENDPOINT`, `list_key == "data_list"`.
    - `test_raw_window_keeps_bounds_and_drops_the_rest`: outer times `START - 1, START, END, END + 1` → `kept` holds the `START` and `END` entries (same objects, service order), `unusable == 0`.
    - `test_raw_window_counts_rows_without_usable_time`: outer `time` absent, `"x"`, `True`, and a non-dict entry → `kept == []`, `unusable == 4`.
  - `test_domain_spo2.py` (same fake + `payload_with_rows`, values `{"time": t, "spo2": n}`):
    - `test_spo2_domain_is_registered`: `REGISTRY["spo2"] is fetch_spo2_window`.
    - `test_spo2_window_asks_a_day_before_start`: `data_obj == {"key": "spo2", "start_time": START - 86400, "end_time": END, "reverse": True}`.
    - `test_spo2_window_keeps_rows_at_both_bounds`: rows at `START` and `END` → `[Spo2Point(END, 98), Spo2Point(START, 97)]` in service order, `notes == []`.
    - `test_spo2_window_drops_rows_outside_the_asked_window`: rows at `START - 1` and `END + 1` are in neither `records` nor `raw`; `notes == []`.
    - `test_spo2_without_usable_value_is_skipped_with_note`: `spo2` absent, `0`, `"97"` → no record, three notes containing `"usable SpO2 value"`, all three values still in `raw`.
    - `test_spo2_bad_value_and_bad_time_are_noted`: value `"not json"` → one note, not in `raw`; outer `time` `"x"` → one note containing `"usable time"`, not in `raw`.
    - `test_spo2_empty_window_yields_no_records_and_no_notes`.
  - `test_client.py`: `test_spo2_delegates_to_domain_with_local_day_bounds`; `test_spo2_is_registered_once_client_is_imported`; `'spo2'` added to the expected registry list; `mifit.Spo2Point` in `test_package_exports`.
  - `test_cli.py`: `SPO2_POINT = Spo2Point(epoch_s=1790812860, spo2_pct=97)`; `FakeMiFit.spo2`; `test_export_spo2_writes_records`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_raw_window.py tests/test_domain_spo2.py tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `raw_window.py`, the `heart_rate` refactor, `Spo2Point`, `fetch_spo2_window`, the facade method, its import in `client.py`, the export and the docstrings.
- [ ] **Step 4: Run** `python -m pytest tests/test_domain_heart_rate.py -q` with that file unchanged (`git diff --quiet tests/test_domain_heart_rate.py`), then `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Write `notes/11-spo2-weight-profile.md`** (synthetic values; counts and relative clock offsets only; no dates, ids or values from the account):
  - §1 血氧：APK 类与 key; 日汇总 `spo2` 形状与含义; 原始 `spo2` / `single_spo2` / `low_spo2` 形状; §1.3 首桶丢失实验 (the 36 / 27 / 36 comparison and the [H, H+2h) offsets from "Live-verified shapes").
  - §2 体重：key `weight` 的 APK 依据 (`pullOneYearWeightIfNeed`); 形状; kg 与 BMI 的交叉核对; 身体成分全 0.
  - §3 用户档案：入口 (`healthapp/` + 去前缀签名、无 `data`)、`result` 字段、哪些是个人标识; set/remove 是写接口，永不调用.
  - §4 未决: bucket size for SpO2 on other accounts; whether `weight` / `single_spo2` / `low_spo2` are bucketed (weight and spo2 are read through the margin anyway; `MiFit.raw` is not); `max_spo2` / `min_spo2` raw keys unprobed; `low_spo2` shape unseen; sleep-goal unit and `regular_goal_list` field 4.
  - §5 SDK 落地: one bullet now (`spo2_daily`, `spo2`, `raw_window`); Tasks 3 and 4 add theirs.
- [ ] **Step 6: Commit** `git add src/mifit/domains/raw_window.py src/mifit/domains/spo2.py src/mifit/domains/heart_rate.py src/mifit/models/spo2.py src/mifit/client.py src/mifit/__init__.py src/mifit/cli/__init__.py tests/test_domain_raw_window.py tests/test_domain_spo2.py tests/test_client.py tests/test_cli.py notes/11-spo2-weight-profile.md && git commit -m "feat(domains): raw SpO2 points via a shared day-early window read (spo2)"`

---

### Task 3: `WeightRecord` + `weight` domain, exposed

**Files:**
- Create: `src/mifit/models/weight.py`, `src/mifit/domains/weight.py`, `tests/test_domain_weight.py`
- Modify: `src/mifit/domains/base.py` (add `as_float`), `tests/test_domain_base.py`, `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (docstring list), `tests/test_client.py`, `tests/test_cli.py`, `notes/11-spo2-weight-profile.md` (§5 bullet)

**Interfaces:**
- Consumes: `fetch_raw_window(t, key, start, end) -> tuple[list[dict], int]` (Task 2).
- Produces:
  - `domains/base.py`: `def as_float(raw: object) -> float | None` — a reported number (`0` included) as `float`; `None` for a `bool`, a non-number or a non-finite float.
  - `models/weight.py`: `@dataclass class WeightRecord(epoch_s: int, weight_kg: float, bmi: float | None)`. Docstring: one weighing; `epoch_s` the time it was recorded (Unix seconds), `weight_kg` body weight in kilograms, `bmi` the body-mass index stored with it (`None` when not reported). Body composition is deliberately absent (unverified; in `raw`).
  - `domains/weight.py`: `@register("weight") def fetch_weight(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult`. Reads `fetch_raw_window(t, "weight", …)`. Per kept entry: bad JSON / non-object → note, not in `raw`; else into `raw`, then `weight_kg = as_float(value.get("weight"))`, and a weight that is not `> 0` is skipped with `"Skipped a weight row without a usable weight."`; `bmi = as_float(value.get("bmi"))` when `> 0`, else `None`; `epoch_s` = outer `time`. One `"Skipped a weight row without a usable time."` per unusable row.
  - `MiFit.weight(self, start: DateArg, end: DateArg) -> DomainResult` (local-day bounds as the other methods). Docstring: every weight record in the range, with kg and BMI; body-composition figures stay in `raw` (unverified, zero on the account checked); a long range is fine (records are sparse).
  - `mifit.WeightRecord` in `__all__`; `mifit export weight`.

- [ ] **Step 1: Write failing tests.**
  - `test_domain_base.py`: `test_as_float` (parametrized): `70.5→70.5`, `70→70.0`, `0→0.0`, `True→None`, `"70"→None`, `None→None`, `float("inf")→None`.
  - `test_domain_weight.py` (fake transport as in Task 2; value = the "Live-verified shapes" weight value with `time = T = 1600000000`, row outer time `T`; `START = T - 86400`, `END = T + 86400`):
    - `test_weight_domain_is_registered`.
    - `test_weight_asks_a_day_before_start`: `data_obj == {"key": "weight", "start_time": START - 86400, "end_time": END, "reverse": True}`.
    - `test_maps_weight_and_bmi`: `records == [WeightRecord(epoch_s=T, weight_kg=70.5, bmi=23.0)]`, `notes == []`.
    - `test_int_weight_becomes_float`: `"weight": 70` → `weight_kg == 70.0` and `isinstance(weight_kg, float)`.
    - `test_row_without_usable_weight_is_skipped_with_note`: `weight` absent, `0`, `"70"` → no record, three notes containing `"usable weight"`, all three values in `raw`.
    - `test_missing_or_zero_bmi_becomes_none`.
    - `test_composition_stays_out_of_the_record`: `{f.name for f in dataclasses.fields(WeightRecord)} == {"epoch_s", "weight_kg", "bmi"}`, and `raw[0]["body_fat_rate"] == 0.0` (A keeps it).
    - `test_rows_outside_the_window_are_dropped`: outer times `START - 1`, `END + 1` → no record, no `raw`, no note.
    - `test_empty_range_yields_no_records_and_no_notes`.
  - `test_client.py` / `test_cli.py`: as Task 2 for `weight` (`WEIGHT = WeightRecord(epoch_s=1790812800, weight_kg=70.5, bmi=None)`, `test_export_weight_writes_records`, registry list gains `'weight'`, package export).
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_weight.py tests/test_domain_base.py tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `as_float`, the model, `fetch_weight` (module docstring: key `"weight"` per APK `FitnessPersistKey.Weight`; the window goes through `raw_window`; composition A-only, all zero on the account checked, notes/11 §2), facade, import, export, docstring list; add the §5 bullet to notes/11.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git commit -m "feat(domains): weight records -> WeightRecord (weight)"` with the files above.

---

### Task 4: Profile read (`healthapp/` base path) + `MiFit.profile()` + `mifit profile` + roadmap

**Files:**
- Create: `src/mifit/models/profile.py`, `src/mifit/domains/profile.py`, `tests/test_domain_profile.py`
- Modify: `src/mifit/transport.py`, `tests/test_transport.py`, `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py`, `tests/test_client.py`, `tests/test_cli.py`, `notes/11-spo2-weight-profile.md` (§5 bullet), `docs/superpowers/roadmap.md`

**Interfaces:**
- Consumes: `as_float`, `as_positive_int`, `DomainResult`.
- Produces:
  - `Transport.get_json(self, endpoint_path: str, data_obj: dict | None, *, sign_path: str | None = None, base_path: str = BASE_PATH) -> dict`. The URL is `BASE_HOST + base_path + endpoint_path`, regionalized as today; the default signature path is that URL's path. `data_obj=None` signs and sends no `data` parameter (`encrypt_query_params(params={}, …)`). Both reach the URL-building code (`_send`, after T2) unchanged otherwise, so the 401 renewal retry applies to them too. Docstring says both.
  - `models/profile.py`: `@dataclass class UserProfile(height_cm: float | None, weight_kg: float | None, step_goal: int | None, calorie_goal_kcal: int | None)`. Docstring: the account's body measurements and daily goals as set in the app; `weight_kg` is the weight stored in the profile (it matched the latest weighing on the one account checked); `calorie_goal_kcal` is the daily calorie goal; each is `None` when not set.
  - `domains/profile.py` (not registered: no time window): `def fetch_profile(t: Transport) -> DomainResult` → `t.get_json("user/get_miot_user_profile", None, sign_path="/user/get_miot_user_profile", base_path="healthapp/")`. When `payload["result"]` is a dict: `raw == [result]`, `records == [UserProfile(height_cm=_positive(as_float(result.get("height"))), weight_kg=_positive(as_float(result.get("weight"))), step_goal=as_positive_int(result.get("daily_step_goal")), calorie_goal_kcal=as_positive_int(result.get("daily_cal_goal")))]` (`_positive` keeps `> 0`, else `None`). Otherwise: empty records and raw, note `"The profile answer carried no profile object."`. Module docstring: the endpoint and signing rule (notes/11 §3), that `user/set_…` / `user/remove_…` exist and are never called, and that `raw` holds personal identifiers.
  - `MiFit.profile(self) -> DomainResult` → `fetch_profile(self._transport)`. Docstring: height, weight and step/calorie goals; `raw` holds the whole profile, including name, birth date and account ids, so treat it as personal data.
  - CLI: `mifit profile [--json PATH]` writes `{records, raw, notes}` exactly like `export` (notes to stderr, JSON to stdout or the file, same error handling and exit codes). Extract the output and error-handling parts of `_cmd_export` into helpers both commands use; the existing export tests stay unchanged. The module docstring names the new command.
  - `mifit.UserProfile` in `__all__`.

- [ ] **Step 1: Write failing tests.**
  - `test_transport.py`:
    - `test_base_path_replaces_app_v1_and_is_regionalized`: `get_json("user/x", DATA_OBJ, base_path="healthapp/")` → URL path `"/healthapp/user/x"`, netloc `hlth.io.mi.com`; params equal `encrypt_query_params(path="/healthapp/user/x", params={"data": COMPACT_DATA}, …)`; with `region="de"` the netloc is `de.hlth.io.mi.com`.
    - `test_none_data_sends_no_data_param`: `get_json(ENDPOINT, None)` → `set(params) == {"rc4_hash__", "signature", "_nonce"}` and params equal `encrypt_query_params(method="GET", path=SIGN_PATH, params={}, nonce=…, ssecurity=…)`.
    - `test_base_path_and_sign_path_combine`: `get_json("user/get_miot_user_profile", None, sign_path="/user/get_miot_user_profile", base_path="healthapp/")` → params equal `encrypt_query_params(path="/healthapp/user/get_miot_user_profile", params={}, signature_path="/user/get_miot_user_profile", …)`.
    - The existing tests, untouched, prove the default request is unchanged.
  - `test_domain_profile.py` (a `Transport` subclass whose `get_json(endpoint_path, data_obj, *, sign_path=None, base_path=…)` records its arguments and returns `{"code": 0, "message": "ok", "result": <synthetic profile from "Live-verified shapes">}`):
    - `test_profile_requests_healthapp_without_data`: one call, `("user/get_miot_user_profile", None, sign_path="/user/get_miot_user_profile", base_path="healthapp/")`.
    - `test_maps_basics`: `records == [UserProfile(height_cm=175.0, weight_kg=70.0, step_goal=8000, calorie_goal_kcal=300)]`, `notes == []`.
    - `test_raw_keeps_the_profile_untouched`: `raw == [result]`, including `name`, `birth`, `regular_goal_list`.
    - `test_missing_or_zero_basics_become_none`: `height` absent, `weight` `0`, `daily_step_goal` `"8000"`, `daily_cal_goal` `true` → those fields `None`; no note.
    - `test_answer_without_profile_object_gives_a_note`: `result` absent, `null` or `[]` → `records == []`, `raw == []`, exactly one note.
    - `test_unverified_and_personal_fields_stay_out_of_b`: `{f.name for f in dataclasses.fields(UserProfile)} == {"height_cm", "weight_kg", "step_goal", "calorie_goal_kcal"}`.
    - `test_profile_is_not_registered`: `"profile" not in REGISTRY`.
  - `test_client.py`: `test_profile_delegates_to_domain` (spy on `client_module.fetch_profile`, called once with the client's transport); `mifit.UserProfile` in `test_package_exports`.
  - `test_cli.py`: `PROFILE = UserProfile(height_cm=175.0, weight_kg=70.0, step_goal=8000, calorie_goal_kcal=None)`; `FakeMiFit.profile()` returns `DomainResult(records=[PROFILE], raw=[{"height": 175}], notes=["n1"])`. `test_profile_writes_records` (`mifit profile --json <tmp>` → exit 0, `records == [asdict(PROFILE)]`, `raw == [{"height": 175}]`); `test_profile_prints_json_to_stdout_and_notes_to_stderr`; `test_profile_without_session_tells_to_log_in` (`FakeMiFit.error_on_init = AuthError(...)` → exit 1, stderr has "mifit login").
- [ ] **Step 2: Run** `python -m pytest tests/test_transport.py tests/test_domain_profile.py tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the transport keywords, the model, `fetch_profile`, the facade method and its import, the export, the CLI subcommand with the shared helpers; add the §5 bullet to notes/11.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. `python -m pip install -e . && mifit --help` lists `profile`; `mifit export --help` lists `spo2`, `spo2_daily`, `weight`.
- [ ] **Step 5: Update the roadmap** (`docs/superpowers/roadmap.md`): §0 M5 row "✅ 实现完成（<date>），待真机核对"; §1 add the four reads (待真机核对); §3 M5 a `结果` line (keys and endpoint found, bucket drop applies to raw SpO2 and is fixed through `raw_window`, B fields as decided here, body composition A-only and all zero on the account, profile B = height/weight/step and calorie goals); §6 M5 row answered ("账号上的体重记录为 1–10 条，身体成分全 0 → 只做到能读、结构正确"). README stays unchanged until Task 5.
- [ ] **Step 6: Commit** `git commit -m "feat(transport,profile): read the account profile from healthapp/ (MiFit.profile, mifit profile)"` with the files above, then a separate `git commit -m "docs: roadmap M5 implemented, pending live check"` for the roadmap.

---

### Task 5: Live app check (manual, with the user; deferred by the controller to the end-of-run session) + README

**Files:**
- Create: `docs/superpowers/verification/<run date>-spo2-weight-profile-live.md`
- Modify: `docs/superpowers/roadmap.md` (§0, §1, §3 M5), `README.md` (domain table, export `<domain>` list, `mifit profile`; only what passes); `src/mifit/models/*`, domains and tests only if a field fails

- [ ] **Step 1:** Export, writing outside the repo: `mifit export spo2_daily --from <D-3> --to <D-1> --json "$HOME\mifit-verify\spo2_daily.json"`, `mifit export spo2 --from <D-1> --to <D-1> --json …\spo2.json`, `mifit export weight --from 2015-01-01 --to <D> --json …\weight.json`, `mifit profile --json …\profile.json`.
- [ ] **Step 2:** The user reads from the app:
  - SpO2 page, each day with data: daily average, highest, lowest; for D-1 the chart's shape and first/last sample time.
  - Weight history: every record's date, time and kg (and BMI if shown).
  - Profile and goals: height, weight, step goal, calorie goal; also the sleep goal and the goal list (to identify `regular_goal_list` field 4 and the sleep-goal unit, for later promotion only).
- [ ] **Step 3:** Compare per field: `avg/max/min_spo2_pct` per day; the D-1 points' first/last time and range against the chart; weight count, date/time (`epoch_s` in local time) and kg (and BMI); `height_cm`, `weight_kg`, `step_goal`, `calorie_goal_kcal`. A field that does not match is removed from B (it stays in `raw`) or renamed to what it is, with its test changed; record the decision.
- [ ] **Step 4:** Write the verification doc (match/differ per field, no values beyond what a row needs, no ids). Update roadmap M5 to ✅ (or partial) and the README for the fields that passed. `python -m pytest -q`, commit `docs: SpO2/weight/profile live verification; roadmap M5`. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Names:** domains `spo2_daily` (daily report) and `spo2` (raw points, mirroring `heart_rate` / `heart_rate_daily`), `weight`; facade `MiFit.spo2_daily`, `MiFit.spo2`, `MiFit.weight`, `MiFit.profile`; models `Spo2DailySummary` and `Spo2Point` (`models/spo2.py`), `WeightRecord` (`models/weight.py`), `UserProfile` (`models/profile.py`). SpO2 field names follow `SleepRecord` (`avg_spo2_pct`, …).
2. **Daily SpO2 B = avg / max / min only**, all straight from the service's daily report, `0 → None`. No recomputation from points and no coefficient fitting (roadmap M5, notes/04 §4). `lack_spo2_count` (only zeros seen; APK `lackTimes` meaning unclear), `latest_spo2` (shifted clock) and the two absent totals stay A-only.
3. **Raw SpO2 gets a domain, not just `MiFit.raw`**, because the live check proved the first-bucket drop (9 of 36 rows lost in one window): `MiFit.raw` would silently lose rows and the domain does not. `single_spo2` and `low_spo2` get no domain (spot checks / events whose app presentation is unverified; `low_spo2` was empty); `MiFit.raw` reads them, as `single_heart_rate` in M3.
4. **The margin-and-filter fix becomes one shared helper** (`domains/raw_window.py`), used by `heart_rate`, `spo2` and `weight`. Three copies of a subtle rule would drift; the heart-rate tests, kept unchanged, guard the move. `MiFit.raw` stays unfiltered (M3 decision 7), with its warning extended to `spo2`.
5. **New raw domains take a record's time from the outer `time`**, which equals `value.time` on every live row and is the value the window filter used, so no record can fall outside the asked window. `heart_rate` keeps reading `value.time` (unchanged behavior).
6. **`spo2_daily` copies `heart_rate_daily` instead of sharing a generic daily-report reader.** Four daily-report domains now repeat the same loop; extracting it is left to M7, when the key catalog shows how many more follow.
7. **Weight B = time, kg, BMI.** The key and kg meaning come from the APK and agree with the profile; BMI matched `kg / m²` against the profile height. Every body-composition field is `0` on the only record, so all of them are A-only (roadmap M5). A weight that is absent or `0` is no weighing and is skipped with a note.
8. **Profile B = height (cm), weight (kg), step goal, calorie goal (kcal).** Height and weight are cross-checked with the weight record; the two goals equal `regular_goal_list` fields 1 and 2. The sleep goal (unit unverified), stand goal (`0`), field 4 and personal fields (`name`, `sex`, `birth`, `age`, ids, `region`) stay A-only; Task 5 collects what promoting the sleep goal and field 4 would need.
9. **Profile transport: `base_path` and `data_obj=None` on `get_json`**, rather than a second request path or a hard-coded profile URL. The live request that worked had exactly this shape (no `data`, `healthapp/`, prefix-stripped signature path), and the same two knobs serve M6's `healthapp/service/gen_download_url`. Defaults keep every existing call byte-identical.
10. **The profile is not in `REGISTRY`** (its function takes no window, like `fetch_heart_rate_full`); it is `MiFit.profile()` plus a `mifit profile` subcommand, sharing `export`'s output and error code.
11. **One notes file, `notes/11`,** for SpO2, weight and profile (notes/09 and /10 are taken), created with the SpO2 code in Task 2 and extended by Tasks 3–4.
12. **Live probing for this plan used 10 calls** (daily SpO2; raw SpO2 no-start + window + widened window; `single_spo2`, `low_spo2`, `weight` no-start; a 6-year `weight` window; the profile under `app/v1/` (failed) and under `healthapp/`). Tasks 1–4 make no live call; everything left is the user's app reading in Task 5.
13. **README:** the new reads are listed only after Task 5 verifies them (T1 rule: do not over-claim). The roadmap records "implemented, pending live check" in Task 4, as M3 did.

## Self-Review

1. **Spec coverage**, against roadmap M5 and the task brief:
   - Daily `key=spo2` → `CloudSpo2Report` → B avg/min/max from the service: Task 1 (decision 2).
   - Raw `spo2` / `single_spo2` / `low_spo2`, and whether the bucket drop applies: measured live (applies); `spo2` domain with the shared fix in Task 2; the other two via `MiFit.raw` (decision 3).
   - Weight key located (`weight`, APK + live); B time/kg (+BMI), composition A-only: Task 3 (decision 7).
   - Profile/goal entry point located by decompiling (`healthapp/user/get_miot_user_profile`) and read live with a GET only; B basics: Task 4 (decisions 8–10).
   - Design §9 open item ("`user_info_profile`/目标的确切云端入口") closed in notes/11 §3.
   - Live app check deferred as Task 5; README listing deferred to it; roadmap §6 question answered in Task 4. ✓
2. **Step scan:** every code step names its file, signature, request and pinned values; the conditional outcome (a field failing the app check) is spelled out in Task 5 Step 3. ✓
3. **Type consistency:** `as_positive_int`, `as_float`, `fetch_raw_window(t, key, start, end) -> tuple[list[dict], int]`, `RAW_ENDPOINT`, `START_MARGIN_S`, `Spo2DailySummary(date, epoch_s, avg_spo2_pct, max_spo2_pct, min_spo2_pct)`, `Spo2Point(epoch_s, spo2_pct)`, `WeightRecord(epoch_s, weight_kg, bmi)`, `UserProfile(height_cm, weight_kg, step_goal, calorie_goal_kcal)`, `get_json(..., data_obj: dict | None, *, sign_path, base_path)` are spelled the same in every task. ✓
4. **Review Focus:** five lines, each pinned by named tests in Tasks 1–4. ✓
5. **Proportion:** five tasks for three small domains, one helper move and one transport keyword pair; no code bodies beyond signatures and assertions. ✓
