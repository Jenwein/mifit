# mifit Long-Tail Keys + Domain Catalog Implementation Plan (M7, first pass)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every fitness-data key readable without new code, write down which keys exist, and promote one key whose meaning is clear to B. Concretely: two generic A-layer reads, `MiFit.raw_window(key, …)` (raw key, complete at the window start) and `MiFit.daily_report(key, …)` (any aggregated-report key), with a CLI `mifit raw <key>`; a controlled catalog `notes/domain-catalog.md` guarded by a test; and one new B domain, `stress_daily`.

**Architecture:** A new module `domains/passthrough.py` holds two key-parameterized reads. They are not registered domains, because a registered domain takes `(t, start, end)` and nothing else. `fetch_raw_rows` reads through the existing `fetch_raw_window` (it asks a day early, then filters). `fetch_daily_rows` uses steps' day window (`report_window`). Both return a `DomainResult` with `records == []` and `raw` = the service's outer rows, untouched except that `value` is JSON-decoded. `stress_daily` copies the `spo2_daily` pattern. The catalog is a Chinese notes file whose tables a small test checks against `REGISTRY`. `MiFit.raw` stays exactly as it is.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport`, `fetch_raw_window`, `domains.days`, `DomainResult`, `register`, `as_int`, `as_positive_int`, `stalled_note`, T3's shared row decoder, `mifit.chunks`), argparse, pytest. No new dependencies.

**Spec:**
- Roadmap `docs/superpowers/roadmap.md` §3 M7:
  - "系统性枚举 app 用到的全部 key（APK 中的 `FitnessPersistKey` 常量 + 设计 §6 候选…），低频逐个探测";
  - "受控的数据域目录（建议 `notes/domain-catalog.md`）：key ↔ 接口 ↔ value 结构 ↔ 是否核对 ↔ 是否有 B 层";
  - "默认只做 A 层（通过 `raw` 或一个通用透传数据域）；只有核对过含义的才加 B 层";
  - "持续进行，不追求一次完备".
- Design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md`: §2 anti-goals (B must not drift; account-risk), §3 (risk control; protocol changes, so key lists are kept central), §6 (the candidate list and the operational definition of "全量"), §9 (key list open), §12 (neutral B names).
- Evidence:
  - `notes/04` §2: endpoints and value classes.
  - `notes/06`: daily-report day window `start_time < time <= end_time`; `time` is the UTC midnight of the local date.
  - `notes/08` §2.3: raw first-bucket drop.
  - `notes/11` §1.3: the same drop on raw `spo2`; `domains/raw_window.py`.
  - The probe below.

**Precondition:** T3 (long-range export, incl. Task 7's shared row decoder and `as_positive_float`) and M6 (`workout_detail`, `notes/12`) are merged into `main`. At that point the registered domains are steps, heart_rate, heart_rate_daily, consumption, sleep, workouts, spo2_daily, spo2, weight and workout_detail, and these exist: `MiFit.raw`, `MiFit.profile`, `MiFit.read_chunks`, `mifit.chunks` (`DOMAIN_CHUNK`), `tests/support.py` (`PagedFake`, `WindowFake`), `domains/raw_window.py` (`fetch_raw_window`, `RAW_ENDPOINT`, `RawWindow(kept, unusable, stalled)`), and `domains/base.py` (`as_int`, `as_positive_int`, `as_float`, `stalled_note`). The CLI has `_LONG_RANGE_DAYS = 92` and `--long`. If T3 Task 7's decoder is not in `base.py`, decode inline exactly as `spo2_daily.py` does. Work goes onto a short branch that is fast-forwarded into `main`.

## Live findings (UTC+8 account, 2026-10-14; 37 read-only calls ≥ 2 s apart, no 401; every value below is synthetic)

Recorded per call: whether it answered, row counts in buckets (0 / 1–10 / >10), field names and JSON types, and booleans. No values, dates or ids were kept (`work/m7/summary.jsonl`, git-ignored). The calls were:
- 3 tag-only aggregated reads;
- 8 `get_latest_fitness_data` reads, 2 of which failed;
- 16 raw 7-day reads;
- 4 first-bucket checks;
- 6 consistency reads.

**APK (3.59.1).** `FitnessPersistKey` (`fit.fitness.export.data`; a near-copy lives in `health_kit_sdk.restapi`, which adds `data_collector`) has 224 string constants:
- About 170 are sport labels (`outdoor_running`, `badminton`, …). These are the outer `key` of sport records (notes/10).
- The other 51 are health keys, listed in the catalog below. Two more names sit at the edges: `passion_cheer` reads as a sport/cheer label (not probed), and `data_collector` exists only in the health_kit copy.

Other APK sources:
- `CloudKey.getCloudRequestKey` maps the app's home data types to aggregated keys: `steps, calories, sleep, heart_rate, stress, spo2, intensity, valid_stand, energy, goal, pai, blood_pressure, blood_sugar, headset, weight, vo2_max, menstruation`.
- `CloudParamsKt` has three tags: `daily_report`, `daily_fitness` and `daily_mark`.
- `CloudDailyHelper` uploads daily reports for 10 types: steps, calories, sleep, heart_rate, stress, spo2, intensity, valid_stand, blood_pressure, vo2_max.

Aggregated value classes (`@SerializedName`):
- `CloudStressReport{avg_stress, max_stress, min_stress, stress_scale: {relax, mild, moderate, severe}, stress_total?, stress_time_total?}`, uploaded only when `maxStress > 0`.
- `CloudStandReport{count}`.
- `CloudHMStrengthReport{duration, process}`.
- `CloudCaloriesReport{calories, goal?}`.
- `CloudVo2MaxReport{avg_/max_/min_vo2_max, …}`.
- `CloudBloodPressureReport{…}`.

Raw value classes:
- `StressItem{time, stress}`
- `CaloriesItem{time, calories}`
- `ValidStandItem{start_time, end_time}`
- `MHStrengthItem{time}`
- `VitalityItem{date_time, daily_low/medium/high_intensity_vitality, latest_accumulated_vitality, suggested_activity_type/duration}`
- `TrainingLoadItem{date_time, current_day_train_load, current_train_load_level, wtl_sum, wtl_sum_optimal_min/max, wtl_sum_overreaching}`
- `Vo2MaxItem{time, vo2_max, vo2_max_level}`
- `ActiveStageItem{start_time, end_time, type, steps, distance, calories, rise_height}`
- `AbnormalRecordItem{start_time, end_time, threshold, items[{time, value}]}`
- `TemperatureItem{time, body_temperature, skin_temperature}`
- `AbnormalFibItem{start_time, end_time}`
- `OneDayPaiReport{date_time, daily_pai, total_pai, low/medium/high_zone_pai}`
- `HeadsetItem{start_time, end_time, high}`
- `LightValueItem{time, light_sensitivity_value}`
- `BodyMomentumItem{time, body_momentum}`
- `GradePredictionItem{time, five_/ten_kilometre_…, half_/full_marathon_grade_prediction_duration}`
- `RunningIndicatorItem{time, running_ability_index, running_ability_level}`

**Aggregated endpoint, tag only (no `key`), last 7 local days, `limit` 100:** one page each, `has_more=false`.
- `daily_report` answers 8 keys:
  - `steps {steps, distance, calories}`
  - `calories {calories}`
  - `heart_rate` (CloudHrReport)
  - `sleep` (CloudSleepReport)
  - `spo2` (CloudSpo2Report)
  - `stress {avg_stress, max_stress, min_stress, stress_scale{relax, mild, moderate, severe}}`
  - `intensity {duration}`
  - `valid_stand {count}`

  The outer row is `{tag, key, time, value, update_time, watermark, source_sid_list, sid}`, and `time` is always a UTC midnight.
- `daily_fitness` answers only `goal {date_time, timeInZero, goal_items[{field, target_value, achieved_value}], sidList?}`. Its outer `time` is also a UTC midnight, and `source_sid_list` is sometimes absent.
- `daily_mark` answers one row per day and key, with value `{has_data: bool}`, for `steps, calories, heart_rate, intensity, sleep, spo2, stress, valid_stand, headset`. It is a per-day index of which keys have data.

**`data/get_latest_fitness_data`** (`LatestFitnessDataParam{params: [LatestFitnessDataModel{key, limit, time}]}`):
- It needs `time`. Without it the answer is `code -8`.
- 51 keys in one request also gave `-8`. Batches of 10 with `time = now`, `limit = 1` answered `{data_list}`, with at most one latest row per key that has data.
- 51 keys were asked: every health key except `spo2` (already known), plus `data_collector`. **22 have data**:
  - New to the SDK: `calories, single_stress, stress, training_load, valid_stand, vitality, vo2_max, dynamic, high_heart_rate, intensity, grade_prediction, headset, light_sensitivity_value, running_ability_index, body_momentum`.
  - Already used by the SDK: `heart_rate, resting_heart_rate, single_heart_rate, single_spo2, sleep, steps, weight`. Raw `spo2` has data too (notes/11).
- 29 keys have no data, or the service does not know them; the two cannot be told apart:
  - stress, PAI and energy: `energy, high_stress, pai`;
  - heart rhythm and AFib: `abnormal_heart_beat, abnormal_heart_rate, low_heart_rate`;
  - temperature: `single_temperature, temperature_characteristic, temperature_trend`;
  - fitness: `physical_fitness_status, lactate_threshold`;
  - noise and goal: `noise, goal` (raw);
  - blood pressure, blood sugar and ECG: `blood_pressure, blood_sugar, single_blood_sugar, mc_blood_pressure, mc_ecg`;
  - women's health: `menstruation, menstrual_symptoms`;
  - report extremes: `max_heart_rate, min_heart_rate, max_spo2, min_spo2`;
  - others: `data_collector, low_spo2, watch_daytime_sleep, watch_night_sleep, unknown`.

**Raw 7-day reads** (asked a day early, first page only, `reverse=true`). Every one answered with `{data_list, has_more, next_key}`, and every outer row was `{key, sid, time, update_time, value, zone_name, zone_offset}`; `training_load` rows lack `zone_offset`. Results:

| key | rows in 7 d | value fields | outer `time` |
|---|---|---|---|
| `stress` | >10 (more pages) | `time, stress` | = `value.time` |
| `single_stress` | 1–10 | `time, stress` | = `value.time` |
| `calories` | >10 | `time, calories` (int or float) | = `value.time` |
| `steps` | >10 | `time, steps, distance, calories` | = `value.time` |
| `valid_stand` | >10 | `start_time, end_time` | (no `value.time`) |
| `intensity` | >10 | `time` | = `value.time` |
| `headset` | >10 | `start_time, end_time, high` (bool) | — |
| `light_sensitivity_value` | >10 | `time, light_sensitivity_value` | = `value.time` |
| `body_momentum` | >10 | `time, body_momentum` | = `value.time` |
| `vitality` | 1–10 | VitalityItem fields | UTC midnight = `value.date_time` |
| `training_load`, `vo2_max`, `dynamic`, `high_heart_rate`, `grade_prediction`, `running_ability_index` | 0 (latest row is older) | as the latest read: TrainingLoadItem (no `wtl_sum_optimal_*`), `time, vo2_max, vo2_max_level`, ActiveStageItem (wire `type`), `start_time, end_time, threshold, items[{time,value}]`, GradePredictionItem, RunningIndicatorItem | `training_load`: UTC midnight |

`resting_heart_rate` rows (latest read) also carry a UTC-midnight `time` equal to `value.date_time`. These are "daily-type" raw keys.

**First-bucket drop** (notes/08), checked with a 4 h window starting at a local midnight (an even UTC hour), plain vs. a day early:
- **`stress`: drop present.** The first 2 h had rows in the day-early read and none in the plain one; the rest was identical.
- **`calories`: drop present**, the same way.

Together with `heart_rate` and `spo2`, that makes 4 of 4 dense keys checked.

**Consistency on one complete local day** (booleans only):
- daily `stress`:
  - `min ≤ avg ≤ max`, all within 1–100;
  - `max` = max of the day's raw `stress` points, `min` = min, and `avg` = the rounded mean (the raw day was complete; no point ≤ 0);
  - the `stress_scale` sum is ≤ 1440.
- daily `valid_stand.count` = the number of distinct local hours with a raw `valid_stand` interval (≠ the number of intervals).
- daily `intensity.duration` = the number of raw `intensity` rows that day.
- daily `calories.calories` = `diet/user_consumption`'s `exercise` (the SDK's `active_kcal`). It is ≠ steps' daily `calories`, and ≠ exercise + basal.

Synthetic examples for tests: daily stress value `{"avg_stress": 35, "max_stress": 80, "min_stress": 12, "stress_scale": {"relax": 600, "mild": 300, "moderate": 60, "severe": 5}}`; raw stress row `{"key": "stress", "sid": "synthetic", "time": 1760400600, "update_time": 1760401000, "value": "{\"time\": 1760400600, \"stress\": 30}", "zone_name": "Asia/Shanghai", "zone_offset": 28800}`.

## Global Constraints

- **Read-only, no live calls in Tasks 1–4.** Task 5 is the deferred app comparison. Never call `up_*`, `delete_*` or any POST.
- **No secrets, identifiers or health values in the repo.** The catalog records names, endpoint labels, JSON field names, bucketed counts and booleans only. Tests use synthetic values.
- **Tests stay offline** (`tests/conftest.py` blocks the network and moves HOME). `python -m pytest -q` is green after every task. The baseline is what it reports before Task 1.
- **A + B.** Generic reads return the service's rows (outer fields intact, `value` JSON-decoded) and never a B record. B models use neutral names; no Mi field name or code appears in `models/`.
- **Anti-goal (design §2):** these stay A-only:
  - `stress_scale` (band durations, unit unverified);
  - daily `valid_stand` / `intensity` / `calories` / `goal`;
  - every other key in the catalog.
- **Backward compatible:** these are unchanged: `MiFit.raw` (signature, request, return type, warning), `fetch_raw_window`, every registered domain, `mifit export` flags and output, and `PagedResult`.
- **Days for `stress_daily` and `fetch_daily_rows` are exactly steps' days:** the window is `report_window(*local_day_span(start, end, tz))`; for `stress_daily`, `date = report_day(row.time)` and `epoch_s = local_midnight(date, tz)`.
- **The catalog is Chinese**, like notes/01–12.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Pushes are done by the controller.

## Review Focus

- **A raw window starting at a local midnight for a bucketed key whose first 2 h hold data** (`stress`, `calories`): every row in the window is returned, and margin rows do not leak. → Task 1 `test_raw_rows_ask_a_day_early_and_keep_the_window`.
- **A row whose `value` is JSON but not an object** (a list, a string), **or that Python's `json` cannot decode** (`"not json"`, a 5000-digit integer literal): it is kept, never dropped or crashed on. Only the undecodable rows get a note. The input row is not mutated. → Task 1 `test_raw_rows_keep_non_object_json_values`, `test_raw_rows_keep_undecodable_value_with_note`, `test_rows_are_copies`.
- **A daily-type raw key** (outer `time` = the UTC midnight of the local date, e.g. `vitality`) **read in a zone west of UTC:** the filter is on the outer `time` as served, with no reinterpretation. The docstring and catalog say the row may then fall in the neighboring local day's window. → Task 1 `test_raw_rows_filter_on_outer_time_only`.
- **`mifit raw` asked for more than 92 days, or `--tag` without `--daily`:** a usage error (exit 2) before any client opens or any request is sent. → Task 2 `test_raw_long_range_needs_long`, `test_raw_tag_without_daily_is_a_usage_error`.
- **A daily stress row with an absent, `0`, string or bool figure:** that field is `None`, the others are kept, with no note and no crash. → Task 3 `test_missing_or_zero_figures_become_none`, `test_non_numeric_figures_become_none`.

---

### Task 1: Generic A-layer reads (`domains/passthrough.py`, `MiFit.raw_window`, `MiFit.daily_report`)

**Files:**
- Create: `src/mifit/domains/passthrough.py`, `tests/test_domain_passthrough.py`
- Modify: `src/mifit/client.py` (two methods, one import, one sentence in `raw`'s docstring), `src/mifit/domains/raw_window.py` (module docstring: "`MiFit.raw_window` reads any raw key through here"), `tests/test_client.py`

**Interfaces:**
- Consumes: `fetch_raw_window(t, key, start_epoch_s, end_epoch_s) -> RawWindow(kept, unusable, stalled)`; `Transport.get_paged_result(...) -> PagedResult(items, truncated, stalled)`; `days.local_day_span`, `days.report_window`; `stalled_note`; `DomainResult`.
- Produces in `domains/passthrough.py`:
  - `AGGREGATED_ENDPOINT = "data/get_aggregated_fitness_data_by_time"`.
  - `fetch_raw_rows(t: Transport, key: str, start_epoch_s: int, end_epoch_s: int) -> DomainResult`. It calls `fetch_raw_window` once. `raw` holds the kept rows in service order, each decoded (below), and `records == []`. Notes, in this order:
    - when `unusable > 0`: `f"Skipped {n} '{key}' row(s) without a usable time."`;
    - when any value could not be decoded: `f"Kept {n} '{key}' row(s) whose value could not be decoded as JSON, as the service sent it."`;
    - when stalled: `stalled_note(f"'{key}' rows")`.
  - `fetch_daily_rows(t: Transport, key: str, start_epoch_s: int, end_epoch_s: int, *, tag: str = "daily_report", tz: tzinfo | None = None) -> DomainResult`. It sends one `get_paged_result(AGGREGATED_ENDPOINT, {"tag": tag, "key": key, "reverse": True, "limit": 50, "start_time": s, "end_time": e}, list_key="data_list")` with `(s, e) = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))`. `raw` holds every row in service order, decoded; a non-object row is kept unchanged. `records == []`. Notes: the same undecodable-value note, then `stalled_note(f"'{key}' daily rows")`.
  - Decoding (module-private) works on a dict row whose `value` is a `str`. A successful `json.loads` gives `{**row, "value": decoded}`, whatever JSON type `decoded` is. A `ValueError` keeps a copy with the string and counts toward the note. A `value` that is absent or not a `str` gives a plain copy. Input rows are never mutated. This does not use T3's object-only decoder: A must keep non-object JSON too.
  - Both functions raise `ValueError("key must be a non-empty string")` (and the same for `tag`) for a non-`str`, empty or whitespace-only argument, before any request. There is no check against a known-key list.
  - Not registered in `REGISTRY`.
  - Module docstring:
    - these are the A layer for any key;
    - raw reads ask a day early because the service drops a window's first bucket, seen on `heart_rate`, `spo2`, `stress` and `calories` (notes/08, notes/11, catalog), and applying it to every key only ever adds rows that are then filtered;
    - daily-type raw keys (outer `time` = UTC midnight of the local date) are filtered on that `time` as served, so west of UTC a row can fall in the neighboring local day's window;
    - the key list lives in `notes/domain-catalog.md`.
- Produces in `client.py`:
  - `MiFit.raw_window(self, key: str, start: DateArg, end: DateArg) -> DomainResult` returns `fetch_raw_rows(self._transport, key, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`.
  - `MiFit.daily_report(self, key: str, start: DateArg, end: DateArg, *, tag: str = "daily_report") -> DomainResult` returns `fetch_daily_rows(self._transport, key, to_epoch_s(start), to_epoch_s(end, end_of_day=True), tag=tag)`.
  - Docstrings: what `raw` / `records` / `notes` hold; `goal` lives under `tag="daily_fitness"`; and a pointer to the catalog.
  - `MiFit.raw`'s docstring gains "Use `raw_window` for a window that is complete at its start." Its code and tests are untouched.

- [ ] **Step 1: Write failing tests** in `tests/test_domain_passthrough.py`, using `PagedFake`/`WindowFake` from `support`, the synthetic stress row above, `START = 1760371200` (a UTC+8 local midnight), `END = START + 86399`, `DAY_1 = 1760400000` (a UTC midnight) and `DAY_2 = DAY_1 + 86400`:
  - `test_raw_rows_ask_a_day_early_and_keep_the_window`: rows at outer times `START - 1`, `START`, `START + 3600`, `END`, `END + 1` (served newest first) give `raw` times `[END, START + 3600, START]`. The one call's `data_obj == {"key": "stress", "start_time": START - 86400, "end_time": END, "reverse": True}` on `RAW_ENDPOINT`, `records == []` and `notes == []`.
  - `test_raw_rows_decode_value_and_keep_outer_fields`: `raw[0] == {**ROW, "value": {"time": 1760400600, "stress": 30}}`.
  - `test_raw_rows_keep_non_object_json_values`: values `"[1, 2]"`, `"\"x\""`, `"7"` and a 5000-digit integer literal give `[1, 2]`, `"x"` and `7`, the literal is kept as its string, and only that last row is counted in the undecodable-value note.
  - `test_raw_rows_keep_undecodable_value_with_note`: `"not json"` is kept as the string, and `notes == ["Kept 1 'stress' row(s) whose value could not be decoded as JSON, as the service sent it."]`.
  - `test_rows_are_copies`: after the call, the fixture rows still hold their string values.
  - `test_raw_rows_note_rows_without_usable_time`: rows with `time` `"x"`, missing, and a non-dict entry give `notes == ["Skipped 3 'stress' row(s) without a usable time."]`.
  - `test_raw_rows_note_a_stalled_read`: `PagedFake(stalled=True)` gives `stalled_note("'stress' rows")` in notes.
  - `test_raw_rows_filter_on_outer_time_only`: a `vitality` row whose outer `time` is a UTC midnight inside `[START, END]` is kept, and one at a UTC midnight before `START` is dropped, whatever `value.date_time` says.
  - `test_daily_rows_use_the_report_window`: with `tz=timezone.utc`, `START=DAY_1`, `END=DAY_2 + 86399`, `data_obj == {"tag": "daily_report", "key": "valid_stand", "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}` on `AGGREGATED_ENDPOINT`.
  - `test_daily_rows_pass_another_tag`: `tag="daily_fitness"` puts `"daily_fitness"` in `data_obj["tag"]`.
  - `test_daily_rows_decode_in_service_order`: over `WindowFake`, three daily rows give `raw` newest first with decoded values, and `records == []`.
  - `test_daily_rows_note_a_stalled_read`: `stalled_note("'valid_stand' daily rows")`.
  - `test_key_and_tag_must_be_non_empty_strings`: `""`, `"  "`, `None` and `5` as `key`, and `""` as `tag`, raise `ValueError`, and `fake.calls == []`.
  - In `test_client.py`:
    - `test_raw_window_delegates_with_local_day_bounds`: a spy on `client_module.fetch_raw_rows`, `client.raw_window("stress", "2026-10-01", "2026-10-02")` passes the local midnight of 10-01 and the last second of 10-02.
    - `test_daily_report_delegates_with_tag`: same, with `tag="daily_fitness"` forwarded and `"daily_report"` by default.
    - The existing `test_raw_*` tests stay unchanged.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_passthrough.py tests/test_client.py -q`. Expected: the new tests FAIL (module missing).
- [ ] **Step 3: Implement** `passthrough.py`, the two facade methods, and the two docstring edits.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/domains/passthrough.py src/mifit/domains/raw_window.py src/mifit/client.py tests/test_domain_passthrough.py tests/test_client.py && git commit -m "feat(client): generic A-layer reads raw_window and daily_report for any key"`

---

### Task 2: CLI `mifit raw <key>` + README

**Files:**
- Modify: `src/mifit/cli/__init__.py`, `tests/test_cli.py`, `README.md`

**Interfaces:**
- Consumes: `MiFit.raw_window(key, start, end)` and `MiFit.daily_report(key, start, end, *, tag)` from Task 1, plus T3's `_LONG_RANGE_DAYS` and its span rule (`--to − --from + 1`).
- Produces:
  - Subcommand `raw`: positional `key` (metavar `KEY`, any string), `--from` and `--to` (required, `_date_arg`), `--daily` (store_true: "read the daily report instead of raw rows"), `--tag TAG` ("aggregated tag, with --daily; default daily_report"), `--json PATH` (`_add_json_option`), and `--long` ("allow a range over 92 days: one paged read, which can take minutes").
  - Usage checks in `main` after parsing. Each is `parser.error`, so it exits 2 and opens no client:
    - `--from` later than `--to`;
    - `--tag` without `--daily`;
    - a span over `_LONG_RANGE_DAYS` without `--long`, with the export's message.
  - `_cmd_raw(args)` returns `_write_result(_read(lambda c: c.daily_report(args.key, args.start, args.end, tag=args.tag or "daily_report") if args.daily else c.raw_window(args.key, args.start, args.end)), args.json_path, noun="row")`, with `None` from `_read` giving exit 1.
  - `_write_result(result, json_path, *, noun: str = "record")`: the confirmation counts `len(result.raw)` when `noun == "row"`, and otherwise `len(result.records)` as today: `f"Wrote {n} {noun}(s) to {json_path}."`. The existing export message stays byte-identical.
  - There is no chunking and no `--resume`: one read.
  - Module docstring: add `raw` (rows as the service sent them, with `value` decoded, for any key; see `notes/domain-catalog.md`).

- [ ] **Step 1: Write failing tests** in `test_cli.py`:
  - Setup: `FakeMiFit.raw_window(self, key, start, end)` records `("raw_window", key, start, end, {})`, and `FakeMiFit.daily_report(self, key, start, end, *, tag="daily_report")` records `("daily_report", key, start, end, {"tag": tag})`. Both return `DomainResult(records=[], raw=[ROW_DECODED], notes=[])`.
  - `test_raw_reads_the_window_and_writes_rows`: `raw stress --from 2026-10-01 --to 2026-10-02 --json out` gives the calls `[("raw_window", "stress", "2026-10-01", "2026-10-02", {})]`, the file equals `{"records": [], "raw": [ROW_DECODED], "notes": []}`, and stdout has `"Wrote 1 row(s)"`.
  - `test_raw_daily_reads_the_daily_report`: `--daily` gives `[("daily_report", "stress", "2026-10-01", "2026-10-02", {"tag": "daily_report"})]`.
  - `test_raw_daily_with_tag`: `--daily --tag daily_fitness` gives tag `"daily_fitness"`.
  - `test_raw_tag_without_daily_is_a_usage_error`: exit 2, and `FakeMiFit.instances == []`.
  - `test_raw_long_range_needs_long`: `2026-01-01..2026-04-03` exits 2 with `"--long"` and `"92"` in stderr and no instance; with `--long` it exits 0 with one call.
  - `test_raw_rejects_a_reversed_range`: exit 2.
  - `test_raw_to_stdout_prints_only_json`: stdout parses as JSON, and notes go to stderr as `note: …`.
  - `test_raw_auth_error_points_to_login`: the fake raises `AuthError`, giving exit 1 with `"mifit login"` in stderr.
  - `test_export_message_unchanged`: an existing steps export still prints `"Wrote 1 record(s)"`.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the subcommand, the checks, `_cmd_raw` and the `noun` keyword.
- [ ] **Step 4: Update `README.md`:**
  - Under "Export", add a short "Any key: `mifit raw`" part: the synopsis `mifit raw <key> --from … --to … [--daily [--tag TAG]] [--json PATH] [--long]`, and that it:
    - writes `{records: [], raw, notes}`, where `raw` holds the service's rows with `value` decoded;
    - asks raw keys a day early so the window is complete;
    - reads the daily report with `--daily`;
    - is one read, so ranges over 92 days need `--long` and there is no resume;
    - lists the known keys in `notes/domain-catalog.md`.
  - Under "Python API", add `client.raw_window("stress", …)` and `client.daily_report("valid_stand", …)`, with one sentence each.
  - Keep the `raw` sentence and add "(see `raw_window` for a complete window)".
- [ ] **Step 5: Run** `python -m pytest -q`, then `python -m pip install -e . && mifit raw --help`. Expected: everything passes, and the help lists `--daily`, `--tag` and `--long`.
- [ ] **Step 6: Commit** `git add src/mifit/cli/__init__.py tests/test_cli.py README.md && git commit -m "feat(cli): mifit raw <key> for any raw or daily-report key"`

---

### Task 3: `StressDailySummary` + `stress_daily` domain, exposed

**Files:**
- Create: `src/mifit/models/stress.py`, `src/mifit/domains/stress_daily.py`, `tests/test_domain_stress_daily.py`, `tests/fixtures/stress_daily_sample.json`
- Modify:
  - `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/chunks.py` (`DOMAIN_CHUNK["stress_daily"] = "month"`), `src/mifit/cli/__init__.py` (domain list in the docstring only);
  - `tests/test_client.py`, `tests/test_cli.py`, `tests/test_chunks.py` (a `COMPARISONS` entry).

**Interfaces:**
- Consumes: `get_paged_result`; the `days` helpers; `as_int`, `as_positive_int`, `stalled_note`; T3's shared row decoder; `register`.
- Produces:
  - `models/stress.py`: `@dataclass class StressDailySummary(date: str, epoch_s: int, avg_stress_level: int | None, max_stress_level: int | None, min_stress_level: int | None)`. Docstring:
    - `date` is the local date and `epoch_s` its local midnight;
    - the three figures are the day's stress level (a 1–100 index) as the service's daily report states them, with the average being the service's own;
    - each is `None` when not reported.
  - `domains/stress_daily.py`: `@register("stress_daily") def fetch_stress_daily(t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None) -> DomainResult`.
    - Request: `{"tag": "daily_report", "key": "stress", "reverse": True, "limit": 50, "start_time", "end_time"}` on the aggregated endpoint.
    - Field map: `avg_stress→avg_stress_level`, `max_stress→max_stress_level`, `min_stress→min_stress_level`, each via `as_positive_int`.
    - Notes: `"Skipped a daily stress row whose value is missing or not JSON."`, `"Skipped a daily stress row whose value is not an object."`, `"Skipped a daily stress row without a usable day timestamp."` and `stalled_note("daily stress rows")`.
    - Row handling is exactly `spo2_daily`'s: a decoded value goes to `raw` before the day check, and an all-`None` row still gives a record.
  - `MiFit.stress_daily(self, start: DateArg, end: DateArg) -> DomainResult`. Docstring: one record per local day with stress data, giving the day's average, highest and lowest stress level; time per stress band stays in `raw`.
  - `mifit.StressDailySummary` is in `__all__`. `mifit export stress_daily` works through `REGISTRY`, with no CLI code.
  - Module docstring: steps' day rules (notes/06); APK `CloudStressReport`; one-day evidence that the figures are the min, max and rounded mean of the day's raw `stress` points (`notes/domain-catalog.md`); `stress_scale` stays A-only (band durations whose unit is unverified); app comparison pending.

- [ ] **Step 1: Write the fixture and failing tests.**
  - Fixture: the `spo2_daily` fixture envelope with two synthetic rows. `DAY_1 = 1760400000` holds the synthetic daily stress value above; `DAY_2 = DAY_1 + 86400` has no `min_stress`.
  - `test_stress_daily_domain_is_registered`.
  - `test_requests_daily_report_stress_for_the_window`: `tz=UTC`, and `data_obj == {"tag": "daily_report", "key": "stress", "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}`.
  - `test_maps_day_figures`: `records[0] == StressDailySummary("2025-10-14", DAY_1, 35, 80, 12)` and `notes == []`.
  - `test_epoch_is_the_local_midnight_of_the_report_date`: under UTC+8, `epoch_s == DAY_1 - 28800`.
  - `test_missing_or_zero_figures_become_none`: absent, `null` or `0` for one field gives `None` there only, with no note.
  - `test_non_numeric_figures_become_none`: `"35"`, `true` and `[35]` all give `None`.
  - `test_raw_keeps_decoded_values_untouched`: `stress_scale` is included.
  - `test_row_with_bad_value_is_skipped_with_note`: `"not json"` and `"[1]"`.
  - `test_row_without_usable_day_is_skipped_with_note`.
  - `test_unverified_fields_stay_out_of_the_summary`: the dataclass field set is `{"date", "epoch_s", "avg_stress_level", "max_stress_level", "min_stress_level"}`.
  - `test_empty_window_yields_no_records_and_no_notes`, `test_stalled_read_says_so`.
  - In `test_client.py`:
    - `test_stress_daily_delegates_to_domain_with_local_day_bounds`;
    - the expected list in `test_importing_client_registers_the_domains` gains `'stress_daily'` (sorted, after `'steps'`);
    - `test_package_exports` gains `mifit.StressDailySummary`.
  - In `test_cli.py`: `FakeMiFit.stress_daily` and `test_export_stress_daily_writes_records`.
  - In `test_chunks.py`: add `stress_daily_figures(n) = {"avg_stress": 30 + n % 20, "max_stress": 70 + n % 25, "min_stress": 5 + n % 10}` and the `COMPARISONS` entry `("stress_daily", lambda: WindowFake(daily_reports(stress_daily_figures)), {"data_list"})`. `test_every_registered_domain_has_a_chunk_size` and `test_raw_point_domains_use_week` then pass with `"month"`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_stress_daily.py tests/test_client.py tests/test_cli.py tests/test_chunks.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the model, the domain, the client import and method, the export, the chunk entry and the CLI docstring. The README domain table is left for Task 5, as for M3–M5.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes, and `mifit export --help` lists `stress_daily`.
- [ ] **Step 5: Commit** `git add src/mifit/models/stress.py src/mifit/domains/stress_daily.py src/mifit/client.py src/mifit/__init__.py src/mifit/chunks.py src/mifit/cli/__init__.py tests/test_domain_stress_daily.py tests/fixtures/stress_daily_sample.json tests/test_client.py tests/test_cli.py tests/test_chunks.py && git commit -m "feat(domains): daily stress report -> StressDailySummary (stress_daily)"`

---

### Task 4: `notes/domain-catalog.md` + catalog guard test + roadmap close-out

**Files:**
- Create: `notes/domain-catalog.md`, `tests/test_domain_catalog.py`
- Modify: `docs/superpowers/roadmap.md`, `README.md` (one line under "Known limitations" or "Python API": "Which keys exist and what is verified: `notes/domain-catalog.md`")

**Interfaces:**
- Consumes: `REGISTRY` (after `import mifit.client`); the Live findings above; notes/04, 08, 10, 11, 12 for the rows already covered.
- Produces: the catalog, in this fixed layout (the test parses it):
  - **§0 说明**:
    - purpose (the roadmap's "受控目录", maintained over time, not complete in one go);
    - the legend for the status values below;
    - the method: the APK constant lists, tag-only aggregated reads, `get_latest_fitness_data` in batches of 10, and 7-day raw reads;
    - the account context (one UTC+8 account) and the call counts;
    - privacy: names, buckets and booleans only.
  - **§1 接口一览**, a table `| 接口 | 用途 | 状态 | SDK 入口 |`. The rows:
    - `data/get_fitness_data_by_time`: `MiFit.raw`, `MiFit.raw_window`, `heart_rate`, `spo2`, `weight`, `sleep` (raw part);
    - `data/get_aggregated_fitness_data_by_time`: tags `daily_report` / `daily_fitness` / `daily_mark`; `MiFit.daily_report`, `steps`, `heart_rate_daily`, `spo2_daily`, `sleep`, `stress_daily`;
    - `data/get_sport_records_by_time`: `workouts`;
    - `diet/user_consumption`: `consumption`;
    - `healthapp/user/get_miot_user_profile`: `MiFit.profile`;
    - `healthapp/service/gen_download_url` + FDS: `workout_detail`;
    - `data/get_latest_fitness_data`: found, request shape as above, not wrapped;
    - `*_by_watermark`, `get_stat_data_by_time`, medical and project data: 未探测.
  - **No example values anywhere in the catalog.** `值字段` cells give field names, with JSON types where useful, so the timestamp guard below never trips on a synthetic example.
  - **§2 汇总 key** and **§3 原始 key**, each a table `| key | 接口 | APK 值类 | 值字段（顶层） | 本账号 | 已核对 | B 层 |`.
    - `接口` is one of `raw`, `daily_report`, `daily_fitness`, `daily_mark`.
    - `本账号` is one of `7 天 >10`, `7 天 1–10`, `仅较早数据`, `无`, `未探测`.
    - `已核对` is one of `app 已核对`, `待真机核对`, `内部一致（1 天）`, `—`.
    - `B 层` is the domain name in backticks, `候选`, or `—`.
    - One row per key and endpoint. §2 lists the 8 `daily_report` keys plus `vo2_max` / `blood_pressure` / `energy` / `pai` / `blood_sugar` / `headset` / `weight` / `menstruation` (from APK, no rows), `goal` (`daily_fitness`) and one `daily_mark` row (`*`, `{has_data}`). §3 lists all 51 health keys of `FitnessPersistKey` plus `data_collector` and `passion_cheer` (marked 未探测), with the 23 that have data first (the 22 above plus `spo2`).
    - These daily rows are `候选` B: `stress` raw points, `valid_stand` and `intensity` (with their one-day consistency), and `calories` (= `consumption.active_kcal` on one day).
    - These are noted:
      - the daily-type raw keys (`resting_heart_rate`, `vitality`, `training_load`) and their UTC-midnight `time`;
      - first-bucket drop: `heart_rate`, `spo2`, `stress`, `calories` (yes); others unknown, and `raw_window` applies the fix to all.
  - **§4 运动标签 key**: one paragraph. The ~170 sport labels are sport records' outer `key` (notes/10), not fitness-data keys; the full list is in the APK `FitnessPersistKey`.
  - **§5 未决与下一步**: what the app shows for the candidate keys (Task 5); the 29 keys without data, which a device or feature might fill; the unprobed endpoints; and whether `get_latest_fitness_data` is worth wrapping.
  - **§6 维护规则**: adding a key means one probe of 10 calls at most, ≥ 2 s apart, recording only names, buckets and booleans; update its row; a new B domain updates its row's `B 层`; the guard test enforces that every registered domain appears.
- Guard tests (`tests/test_domain_catalog.py`), reading `Path(__file__).parents[1] / "notes" / "domain-catalog.md"` as UTF-8:
  - `test_every_registered_domain_is_in_the_catalog`: every `REGISTRY` name appears in backticks in the last cell of at least one table row (a line starting with `|` that is not a `|---` separator).
  - `test_catalog_rows_are_unique`: within each table, the pairs of first and second cells are distinct.
  - `test_catalog_holds_no_numbers_from_the_account`: no cell matches `\b1[67]\d{8}\b` (a Unix time in 2020–2026), so no real timestamp slips in.

- [ ] **Step 1: Write `tests/test_domain_catalog.py`.** Run it: FAIL (file missing).
- [ ] **Step 2: Write `notes/domain-catalog.md`** from the Live findings, plus notes/08/10/11/12 for existing domains, with synthetic examples only. Run the guard tests: PASS.
- [ ] **Step 3: Update `docs/superpowers/roadmap.md`:**
  - **§0 M7 row:** `✅ 一轮完成（2026-10-14）；目录 notes/domain-catalog.md 持续维护；stress_daily 待真机核对`.
  - **§1:** add the `stress_daily` bullet and a bullet for `MiFit.raw_window` / `MiFit.daily_report` / `mifit raw`. Update the test count to what `python -m pytest -q` prints.
  - **§3 M7:** add a `结果` line covering:
    - the enumeration method and counts (51 health keys; 23 raw keys with data; 9 aggregated keys over three tags);
    - the generic reads (raw asked a day early, because the drop is confirmed on 4 keys);
    - `stress_daily` B (one-day evidence; app comparison pending);
    - candidates `valid_stand` / `intensity` / `calories`;
    - "目录持续维护，不追求一次完备".
  - **§6:** add "M7：真机核对时读 app 的压力日均/最高/最低、站立小时数、中高强度分钟数".
- [ ] **Step 4: Add the README line.** Run `python -m pytest -q`: everything passes.
- [ ] **Step 5: Commit** `git add notes/domain-catalog.md tests/test_domain_catalog.py docs/superpowers/roadmap.md README.md && git commit -m "docs: domain catalog (M7 first pass) with guard test; roadmap M7"`

---

### Task 5: Live app comparison (manual, with the user; deferred by the controller to the end-of-run session) + README rows

**Files:** `docs/superpowers/verification/<run date>-m7-live.md`, `README.md` (domain table row for `stress_daily`), `notes/domain-catalog.md` (`已核对` / `B 层` cells), roadmap.

- [ ] **Step 1:** `mifit export stress_daily --from <D-3> --to <D-1> --json "$HOME\mifit-verify\stress.json"`; `mifit raw valid_stand --daily …` and `mifit raw intensity --daily …` for the same days.
- [ ] **Step 2:** For each day, the user reads from the app: the stress average, highest and lowest; stand hours; moderate-to-high-intensity minutes; active calories.
- [ ] **Step 3:** Record match or differ per field.
  - A `stress_daily` field that differs leaves B (it stays in `raw`).
  - A candidate that matches is listed in the catalog as `app 已核对` and can be promoted in a later pass.
  - Update the README table and the roadmap. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Two generic reads, not one passthrough domain:**
   - `raw_window` covers raw keys and `daily_report` covers aggregated keys. They are different endpoints with different window rules (bucketed and left-closed by row, vs. left-open by day), and one read cannot be right for both.
   - Neither is a registered domain, so `REGISTRY`, `mifit export` and the chunk policy stay "one name = one meaning".
2. **They return `DomainResult` (`records == []`), not a list like `MiFit.raw`.** Notes are how this SDK reports a stalled read or dropped rows, and the CLI reuses `_write_result` unchanged. `raw` holds whole outer rows (`time`, `zone_offset`, `sid` matter for keys without a `value.time`) with `value` decoded, which fits `DomainResult.raw`'s "decoded but otherwise untouched". `MiFit.raw` keeps its list and warning for compatibility.
3. **The day-early fix applies to every raw key, with no per-key list.**
   - The drop is confirmed on 4 of the 4 dense keys checked (`heart_rate`, `spo2`, `stress`, `calories`).
   - The fix can only add rows, which are then filtered, at a cost of about one extra day per read.
   - A per-key list would be a guess for the other 50 keys.
4. **No key validation against the catalog.** Any non-empty string is sent; the A layer must reach keys that are not catalogued yet. The catalog is documentation, enforced only against `REGISTRY`.
5. **`value` decoding keeps any JSON type, and keeps undecodable strings with a note.** The A layer may not drop data. T3's object-only decoder is for B domains.
6. **`mifit raw` is one paged read, with no chunking or resume.** It takes the export's 92-day `--long` rule (design §3 explicit opt-in). `--tag` is only meaningful with `--daily`. Paging is already paced by the transport (1 s + jitter). Chunked raw-by-key exports can come later if needed.
7. **There is one new B domain, `stress_daily` (avg / max / min stress level).**
   - The APK meaning is plain (`CloudStressReport` built from `DailyStressReport` avg/max/min).
   - On one complete day, the three figures were exactly the max, min and rounded mean of the day's raw points.
   - It is the same pattern as `heart_rate_daily` / `spo2_daily`.
   - It is marked 待真机核对, as M3–M5 domains were.
   - Field names `*_stress_level` avoid the wire names (§12).
8. **Not promoted this pass:**
   - `valid_stand.count` (stand hours?) and `intensity.duration` (MVPA minutes?): one-day internal consistency only, and the app's presentation is unverified.
   - Daily `calories`: it equals `consumption.active_kcal` on one day, so it adds nothing B lacks.
   - Raw `stress` points: the generic read covers them.
   - `vitality`, `training_load`, `vo2_max`, `running_ability_index`, `grade_prediction`: no recent data, so nothing can be checked.
   - In the catalog, daily `valid_stand`, `intensity` and `calories` (§2) and raw `stress` (§3) are `候选` rows. The five keys with little or no recent data are `—` rows, listed in §5 under 只有较早数据的 key. (Corrected in the final polish, 2026-10-15: this line first said every item above became a `候选` row.)
9. **The catalog is `notes/domain-catalog.md`** (the roadmap's name, unnumbered because it is a living document), Chinese, with fixed table columns so `tests/test_domain_catalog.py` can enforce that every registered domain is catalogued and that no Unix timestamp leaks in.
10. **`get_latest_fitness_data` is documented, not wrapped.** It was the cheapest enumeration tool (10 keys per call), but no user-facing need is known yet. The same goes for the three tags' tag-only reads.
11. **Daily-type raw keys** (outer `time` = UTC midnight of the local date) are filtered on that `time` as served. The docstring and catalog say so, and nothing reinterprets them.
12. **README:** the generic reads and `mifit raw` are documented in Task 2: they are a tool, not a claim that a figure is verified. The `stress_daily` row waits for the live check (Task 5), as in M3–M5.
13. **Chunk size:** `stress_daily` takes `"month"`, as every daily-report domain does.
14. **Probe budget:** 37 of 40 calls were used, ≥ 2 s apart, with no 401. Two calls returned `code -8` (`get_latest_fitness_data` without `time`, and with 51 keys). No payload was stored.

## Self-Review

1. **Spec coverage:**
   - "系统性枚举 key（FitnessPersistKey + §6 候选）": Live findings plus catalog §2/§3, covering all 51 health constants and every §6 candidate: stress, PAI, vitality, training load, VO2max, stand, temperature, AFib (`abnormal_heart_beat`) and the `calories` daily report.
   - "低频逐个探测": the probe, 37 calls.
   - "数据域目录 key ↔ 接口 ↔ value 结构 ↔ 是否核对 ↔ 是否 B 层": Task 4's columns.
   - "默认只做 A 层（raw 或通用透传）": Tasks 1–2.
   - "只有核对过含义的才加 B 层": Task 3, Decisions 7–8.
   - "持续进行": catalog §5/§6 and the roadmap wording.
   - notes/08's day-early rule for bucketed raw reads: Task 1, Decision 3.
   - The README and roadmap close-out: Tasks 2 and 4, plus Task 5. ✓
2. **Step scan:**
   - Every code step names its file, signature, request dict and note texts.
   - The catalog's layout and value vocabularies are fixed, because a test parses them.
   - No function body is given beyond the decode rule. ✓
3. **Type consistency:** these names are used identically everywhere: `fetch_raw_rows`, `fetch_daily_rows`, `AGGREGATED_ENDPOINT`, `MiFit.raw_window`, `MiFit.daily_report(..., tag=)`, `_cmd_raw`, `_write_result(..., noun=)`, `StressDailySummary(date, epoch_s, avg_stress_level, max_stress_level, min_stress_level)`, `fetch_stress_daily` and `DOMAIN_CHUNK["stress_daily"]`. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1, 2 or 3. ✓
5. **Proportion:** four implementation tasks plus one deferred manual task. There are no code blocks; tests are named with their pinned values. ✓
