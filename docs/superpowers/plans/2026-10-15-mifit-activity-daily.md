# mifit Activity Daily Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `activity_daily` domain: one neutral `ActivityDailySummary` per local day, holding the three figures of the app's 活力指标 page (activity count, activity minutes, active calories). The figures come from the daily-report keys `valid_stand`, `intensity` and `calories`. The docs then record the 2026-10-15 app check.

**Architecture:** The domain sends three ordinary daily-summary reads through the shared `daily_report.fetch_daily_summaries`, one per key, always in the order `valid_stand`, `intensity`, `calories`. Each read's `summarize` returns a `(day, epoch_s, figure)` tuple. The domain merges those tuples by local date into one record per day that any key reports, newest first. Days, per-row notes, `raw` and the stall caveat all come from the shared reader unchanged, so the day rules are steps' (notes/06). A new shared reading, `as_non_negative_int`, keeps `0` as a real value. The stress band minutes, which already follow that rule, switch to it.

**Tech Stack:** Python 3.12+, existing mifit modules (`daily_report.fetch_daily_summaries`, `DomainResult`, `register`, `chunks`), pytest. No new deps.

**Spec:** the 2026-10-15 app check in "Verified meanings" below; `notes/domain-catalog.md` §2 (the `calories`, `intensity` and `valid_stand` rows, 单日一致性, 候选), §5 and §6 (maintenance rules); `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` §2.8; roadmap §0, §1, §3 M7 and §6; design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` §2 (anti-goals); the pattern in `src/mifit/domains/stress_daily.py`.

**Precondition:** the branch `activity-daily` is cut from `main` at `6f23eae`, the tree is clean, and the suite gives 1213 passed, 2 skipped. After review, the branch is fast-forwarded into `main`.

## Verified meanings (every value in this plan is synthetic)

- **Rows.** Each of the three keys is an ordinary daily-report row (`tag=daily_report`).
  - Outer row: `{tag, key, time, value, update_time, watermark, source_sid_list, sid}`. `time` is the UTC midnight of the local date, `value` is a JSON string, and there is one row per day with data (notes/06, catalog §2).
  - Decoded values, all int on the wire:
    - `valid_stand` → `{"count": 9}` (APK `CloudStandReport`);
    - `intensity` → `{"duration": 45}` (`CloudHMStrengthReport`, which also declares `process`, never seen live);
    - `calories` → `{"calories": 320}` (`CloudCaloriesReport`, which also declares `goal`, never seen live).
- **M7 single-day consistency** (catalog §2):
  - `count` is the number of distinct local hours with a raw `valid_stand` interval, so it lies in 0–24;
  - `duration` is the number of raw `intensity` rows that day (0–1440, i.e. minutes);
  - `calories` equals `diet/user_consumption`'s `exercise`, which is `consumption.active_kcal`.
- **App check (2026-10-15, done live with the user on the 活力指标 page):**
  - `valid_stand.count` is the app's 活动次数;
  - `intensity.duration` is the app's 活动时长, in minutes;
  - `calories.calories` is the app's 卡路里, in kcal.
  - Only "match" was recorded, never a figure.

## Global Constraints

- **Read-only; no live calls in this plan.** The app check is already done.
- **Public repo.** No health figure, id, date of account data or account fact goes into a committed file. Tests use synthetic values only, and the docs record match or differ only.
- **Tests stay offline:** a fake transport and synthetic rows only. `python -m pytest -q` passes after every task. The baseline is 1213 passed, 2 skipped.
- **A + B.**
  - `DomainResult.raw` keeps every decoded value untouched.
  - The model uses neutral names, and no Mi wire key or field name is quoted in `models/` (the same rule as for the other models).
  - Unverified fields (`goal`, `process`) stay out of B (design §2 anti-goal).
- **Days are exactly steps' days**, through the shared reader: the window is `report_window(*local_day_span(start, end, tz))`, `date = report_day(row.time)` and `epoch_s = local_midnight(date, tz)`.
- **`0` is a real value** for all three figures. An absent, negative, `bool` or non-number figure becomes `None`. Nothing is ever fabricated.
- **Backward compatible:** no existing signature, field or behavior changes. In particular, `stress_daily`'s band minutes stay identical.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit. Before each commit, `git diff --check` must be clean and `git ls-files --eol <files>` must show `i/lf`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Ask the user before any push.
- **Doc languages:** notes, the roadmap and the checklist are in Chinese; the README, the CHANGELOG and docstrings are in English. The app's on-screen labels may appear in Chinese.

## Review Focus

- **A day that only some keys report**, e.g. today's partial upload, or a day with stand data but no calories row: it must give one record, with the missing figures `None`, newest first, and no note. → Task 2 `test_day_in_one_key_only_gives_a_record_with_the_others_none`.
- **A day with no activity** (count `0`, `0` minutes, `0` kcal): the figures stay `0`, not `None`. → Task 2 `test_zero_is_a_real_value`; Task 1 `test_as_non_negative_int_keeps_zero_and_positive_numbers`.
- **One key's read is broken** (a bad row, or a stalled cursor): the other keys' figures still arrive, and each note names the figure it concerns, in key order. → Task 2 `test_notes_name_the_figure_and_follow_key_order`, `test_stalled_reads_say_so_per_key`.
- **A long export read in chunks:** it must give the same records as one read, and the same `raw` values (one section per key per chunk). → Task 2's `test_chunks.py` `COMPARISONS` entry.
- **A zone east of UTC:** `date` and `epoch_s` come from the outer `time`, exactly as in the other daily domains. → Task 2 `test_epoch_is_the_local_midnight_of_the_report_date`.

---

### Task 1: `as_non_negative_int`, the shared "0 is real" reading

**Files:**
- Modify: `src/mifit/domains/base.py` (add the reading and list it in the module docstring), `src/mifit/domains/stress_daily.py` (delete `_band_minutes` and use the shared reading), `tests/test_domain_base.py`

**Interfaces:**
- Produces `as_non_negative_int(raw: object) -> int | None` in `domains/base.py`:
  - it reads `raw` with `as_float`, and returns `None` when that gives `None` or a value `< 0`; otherwise it returns `int(value)`;
  - the sign is checked before truncation, so `-0.5` gives `None`, not `0`;
  - docstring: a reported amount (a count, minutes, kcal) for which `0` is a real value, meaning "none that day".
- In `stress_daily._summary`, the four band minutes read `as_non_negative_int(scale.get(...))`. Behavior is unchanged.

- [ ] **Step 1: Write failing tests** in `tests/test_domain_base.py` (add the import):
  - `test_as_non_negative_int_keeps_zero_and_positive_numbers`: parametrized over `(0, 0)`, `(0.0, 0)`, `(0.9, 0)`, `(45, 45)`, `(45.9, 45)` and `(1e3, 1000)`. Each result is equal to the expected value and `type(...) is int`.
  - `test_as_non_negative_int_rejects_negatives_and_non_numbers`: `-1`, `-0.5`, `None`, `True`, `False`, `"30"`, `[30]`, `{"min": 30}`, `nan`, `inf` and `-inf` all give `None`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_base.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement** the reading and switch `stress_daily` to it. Leave `tests/test_domain_stress_daily.py` untouched: `test_band_minutes_keep_zero_and_drop_unusable_values` and `test_band_minutes_are_none_without_a_scale_object` are the regression guard.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/domains/base.py src/mifit/domains/stress_daily.py tests/test_domain_base.py && git commit -m "refactor(domains): shared as_non_negative_int; stress band minutes use it"`

---

### Task 2: `ActivityDailySummary` + `activity_daily` domain, exposed

**Files:**
- Create: `src/mifit/models/activity.py`, `src/mifit/domains/activity_daily.py`, `tests/test_domain_activity_daily.py`
- Modify:
  - `tests/support.py` (`PagedFake` gets `by_key`);
  - `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (the domain list in the docstring only);
  - `src/mifit/chunks.py` (`DOMAIN_CHUNK` and the module docstring), `src/mifit/domains/daily_report.py` (the module docstring's list of daily summary domains);
  - `tests/test_client.py`, `tests/test_cli.py`, `tests/test_chunks.py`;
  - `notes/domain-catalog.md`: in the §2 table, the `calories`, `intensity` and `valid_stand` rows only. `已核对` becomes `app 已核对` and `B 层` becomes `` `activity_daily` ``. This has to happen in this task: the guard `test_every_registered_domain_is_in_the_catalog` fails as soon as the domain is registered.

**Interfaces:**
- Consumes:
  - `fetch_daily_summaries(t, key, start_epoch_s, end_epoch_s, *, what, summarize, tz) -> DomainResult` (`domains/daily_report.py`);
  - `as_non_negative_int` (Task 1); `register`; `DomainResult`.
- Produces:
  - **The model.** `models/activity.py`: `@dataclass class ActivityDailySummary(date: str, epoch_s: int, activity_count: int | None, activity_min: int | None, active_kcal: int | None)`. The field order follows the app page. The docstring says:
    - `date` is the local date and `epoch_s` its local midnight;
    - `activity_count` is the app's activity count (活动次数): a count with no unit, which on the one day checked equaled the number of hours of the day with a recorded stand interval (0–24);
    - `activity_min` is the app's activity time (活动时长), in minutes;
    - `active_kcal` is the app's calories on that page (卡路里), the energy spent on activity in kcal, the same figure as `ConsumptionRecord.active_kcal`;
    - the page is the app's 活力指标 page;
    - `0` is a real value, and each figure is `None` when the service did not report it.
  - **The domain.** `domains/activity_daily.py`: `@register("activity_daily") def fetch_activity_daily(t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None) -> DomainResult`.
  - **The reads**, in this order, as a module-level table:

    | key | value field | B field | `what` |
    |---|---|---|---|
    | `valid_stand` | `count` | `activity_count` | `"daily activity-count"` |
    | `intensity` | `duration` | `activity_min` | `"daily activity-time"` |
    | `calories` | `calories` | `active_kcal` | `"daily active-calorie"` |

    Each read's `summarize(day, epoch_s, value)` returns `(day, epoch_s, as_non_negative_int(value.get(field)))`.
  - **The merge:**
    - there is one record per date found in any key's records, and a figure whose key has no record for that date is `None`;
    - records are sorted newest date first;
    - when one key has two rows for the same date, the first one read gives the figure (`raw` keeps both).
  - **`raw` and `notes`:** `raw` is the three reads' `raw` lists concatenated in table order (the `valid_stand` values, then `intensity`, then `calories`, each in the service's order). `notes` are the three reads' notes concatenated in the same order.
  - **The module docstring** says:
    - the day rules are steps' (notes/06);
    - which APK classes the keys come from;
    - the M7 one-day evidence and the 2026-10-15 app check (the three app labels);
    - the `raw` order and why (one section per key);
    - `goal` and `process` stay in `raw`.
  - **The facade.** `MiFit.activity_daily(self, start: DateArg, end: DateArg) -> DomainResult` returns `fetch_activity_daily(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Docstring: one record per local day that any of the three figures has data for, giving the activity count, the activity minutes and the active calories as the app's activity page shows them, at three requests per call.
  - `mifit.ActivityDailySummary`, exported in `__all__`. `mifit export activity_daily` works through `REGISTRY`, with no CLI code.
  - `DOMAIN_CHUNK["activity_daily"] = "month"`.
  - **The test fake.** `PagedFake(..., by_key=None)`. `by_key` maps a paged request's `data_obj["key"]` to its own envelope, and it is checked before `by_endpoint` and `payload`. A request without a `key` falls through to them.

- [ ] **Step 1: Write failing tests.**
  - **Setup** in `tests/test_domain_activity_daily.py`. There is no fixture file.
    - `TEMPLATE = {"sid": "synthetic", "tag": "daily_report", "key": "synthetic", "value": "{}", "update_time": 0, "watermark": 0}`, and `payload_with_rows = payload_with_rows_from(TEMPLATE)`.
    - `fake(stand=(), intensity=(), calories=(), **kw)` returns `PagedFake(by_key={"valid_stand": payload_with_rows(*stand), "intensity": payload_with_rows(*intensity), "calories": payload_with_rows(*calories)}, **kw)`.
    - Constants: `DAY_1 = 1760400000` (2025-10-14 UTC midnight), `DAY_2 = DAY_1 + 86400`, `START = DAY_1`, `END = DAY_2 + 86399`, `tz=UTC` unless a test says otherwise.
    - `S(date, epoch_s, count, minutes, kcal)` is shorthand for `ActivityDailySummary` with its fields in order.
  - **Tests:**
    - `test_activity_daily_domain_is_registered`.
    - `test_requests_the_three_keys_for_the_window`: there are exactly three calls, in key order. Each one's `data_obj == {"tag": "daily_report", "key": <key>, "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}`, on `"data/get_aggregated_fitness_data_by_time"` with `list_key="data_list"`.
    - `test_merges_the_three_keys_by_day`: each key has rows for DAY_2 and then DAY_1. The values are count 7 and 9, duration 30 and 45, calories 250 and 320. Then `records == [S("2025-10-15", DAY_2, 7, 30, 250), S("2025-10-14", DAY_1, 9, 45, 320)]` and `notes == []`.
    - `test_day_in_one_key_only_gives_a_record_with_the_others_none`: `valid_stand` has DAY_1 only, `intensity` has DAY_2 only, and `calories` has no rows. Then `records == [S("2025-10-15", DAY_2, None, 30, None), S("2025-10-14", DAY_1, 9, None, None)]` (newest first, although the first read gave DAY_1), and `notes == []`.
    - `test_epoch_is_the_local_midnight_of_the_report_date`: one DAY_1 row per key, with `tz=timezone(timedelta(hours=8))`, `START = DAY_1 - 28800` and `END = DAY_2 - 28800 + 86399`, gives `records == [S("2025-10-14", DAY_1 - 28800, 9, 45, 320)]`.
    - `test_zero_is_a_real_value`: `{"count": 0}`, `{"duration": 0}` and `{"calories": 0}` on DAY_1 give `S("2025-10-14", DAY_1, 0, 0, 0)`.
    - `test_missing_negative_or_non_numeric_figures_become_none`: put the `valid_stand` values `{}`, `{"count": None}`, `{"count": -1}`, `{"count": "9"}` and `{"count": True}` on days `DAY_1 + k * 86400` for `k = 0..4`, and read through `END = DAY_1 + 4 * 86400 + 86399`. That gives five records, every one with `activity_count is None`, and `notes == []`.
    - `test_float_figures_are_truncated`: `{"duration": 45.9}` gives `activity_min == 45`, and `type(...) is int`.
    - `test_raw_holds_each_keys_values_in_key_order`: raw is the `valid_stand` values, then the `intensity` values, then the `calories` values, each untouched. The calories value carries an extra `"goal": 500` and the intensity value an extra `"process": [1]`, both kept.
    - `test_notes_name_the_figure_and_follow_key_order`: the `valid_stand` row has outer time `"x"`, the `intensity` value is `"not json"` and the `calories` value is `"[1]"`. Then `notes == ["Skipped a daily activity-count row without a usable day timestamp.", "Skipped a daily activity-time row whose value is missing or not JSON.", "Skipped a daily active-calorie row whose value is not an object."]`, `records == []`, and `raw == [<the valid_stand value>]`.
    - `test_second_row_of_a_key_for_the_same_day_is_ignored`: `valid_stand` rows `(DAY_1, {"count": 9})` and then `(DAY_1, {"count": 3})` give `activity_count == 9`, and `raw` holds both values.
    - `test_unverified_fields_stay_out_of_the_summary`: the dataclass field names are `{"date", "epoch_s", "activity_count", "activity_min", "active_kcal"}`.
    - `test_empty_window_yields_no_records_and_no_notes`: three calls are still sent, and `records`, `raw` and `notes` are all empty.
    - `test_stalled_reads_say_so_per_key`: with `stalled=True` and one DAY_1 row per key, `notes == [stalled_note("daily activity-count rows"), stalled_note("daily activity-time rows"), stalled_note("daily active-calorie rows")]`, and the DAY_1 record is still returned.
  - **`tests/test_client.py`:**
    - `test_activity_daily_delegates_to_domain_with_local_day_bounds`, a copy of the stress test;
    - `test_activity_daily_is_registered_once_client_is_imported`;
    - the expected list in `test_importing_client_registers_the_domains` starts with `'activity_daily', `;
    - `test_package_exports` checks `mifit.ActivityDailySummary is ActivityDailySummary`, and its `__all__` loop checks `"ActivityDailySummary"`.
  - **`tests/test_cli.py`:**
    - add `ACTIVITY_DAY = ActivityDailySummary(date="2026-10-01", epoch_s=1790812800, activity_count=0, activity_min=45, active_kcal=None)`;
    - `FakeMiFit.activity_daily(start, end)` records `("activity_daily", start, end, {})` and returns `DomainResult(records=[ACTIVITY_DAY], raw=[{"count": 0}], notes=[])`;
    - `test_export_activity_daily_writes_records`: `records == [asdict(ACTIVITY_DAY)]`, and the calls are `[("activity_daily", "2026-10-01", "2026-10-02", {})]`.
  - **`tests/test_chunks.py`:**
    - add `activity_daily_figures(n) = {"count": n % 13, "duration": 20 + n % 60, "calories": 150 + n}`;
    - add the `COMPARISONS` entry `("activity_daily", lambda: WindowFake(daily_reports(activity_daily_figures)), {"data_list"})`, with a comment saying that `WindowFake` serves the same rows to all three keys and each read picks its own field;
    - in `test_read_chunks_matches_single_read`, the sleep-only raw branch becomes `if domain in SECTIONED_RAW:`, with `SECTIONED_RAW = {"sleep", "activity_daily"}` and its comment extended: an `activity_daily` read's raw is one section per key, so merged it holds those sections once per chunk.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_activity_daily.py tests/test_client.py tests/test_cli.py tests/test_chunks.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, in this order:
  1. `PagedFake.by_key`;
  2. the model;
  3. the domain;
  4. the client import (it is what registers the domain) and the facade method;
  5. the export;
  6. the `DOMAIN_CHUNK` entry;
  7. the three catalog cells per row.

  Then update the docstrings:
  - `chunks.py`: `activity_daily` joins the daily-report `month` domains (three requests per chunk). Its `raw` joins `sleep` as the exception to "merged raw equals a single read's raw": one section per key, so merged it is the same values in another order.
  - `daily_report.py`: the list becomes `heart_rate_daily`, `spo2_daily` and `stress_daily`, plus `activity_daily`, which calls it once per key.
  - The CLI docstring's domain list gains "activity daily".
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes, the catalog guard included. Then `python -m pip install -e . && mifit export --help` should list `activity_daily`.
- [ ] **Step 5: Commit** `git add src/mifit/models/activity.py src/mifit/domains/activity_daily.py src/mifit/domains/daily_report.py src/mifit/client.py src/mifit/__init__.py src/mifit/cli/__init__.py src/mifit/chunks.py tests/support.py tests/test_domain_activity_daily.py tests/test_client.py tests/test_cli.py tests/test_chunks.py notes/domain-catalog.md && git commit -m "feat(domains): activity count, minutes and calories per day (activity_daily)"`

---

### Task 3: Record the app check (README, CHANGELOG, roadmap, catalog, checklist)

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/superpowers/roadmap.md`, `notes/domain-catalog.md` (prose only), `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md`

**Interfaces:**
- Consumes: the names from Task 2 and the passed count that `python -m pytest -q` printed at the end of Task 2.
- Produces the exact text below. None of it holds a figure from the account.

- [ ] **Step 1: README.**
  - Below the `stress_daily` row of "What it reads today", add ``| `activity_daily` | activity per day, as on the app's 活力指标 page: activity count (活动次数), activity minutes (活动时长), active calories (卡路里) | `ActivityDailySummary` | yes |``.
  - In the Export section, the `<domain>` list starts with `` `activity_daily`, ``.
- [ ] **Step 2: CHANGELOG.** In `## 0.2.0`, under "Added", after the `spo2_daily` / `stress_daily` sub-bullet, add a sub-bullet at the same indent, reading: `activity_daily` (in backticks), then ": the activity count, activity minutes and active calories of the app's 活力指标 page." Nothing is added under Breaking.
- [ ] **Step 3: Roadmap.**
  - §0, M7 row: replace "候选 key 未核对（app 里没找到对应页面）" with "三个候选 key 2026-10-15 在 app 活力指标页核对通过 → 新 B 域 `activity_daily`".
  - §1: after the `stress_daily` bullet, add "  - `activity_daily`：app 活力指标页的活动次数 / 活动时长（分钟）/ 卡路里（`ActivityDailySummary`：`activity_count` / `activity_min` / `active_kcal`，取自日汇总 key `valid_stand` / `intensity` / `calories`，每次读取三个请求；`0` 照实保留，缺失为 `None`；`active_kcal` 与 `consumption.active_kcal` 同值）（notes/domain-catalog.md）。"
  - §1: replace "测试 1213 个通过" with the new passed count.
  - §3 M7, the "真机核对（2026-10-15）" line: replace its last sentence ("候选 key … 仍未核对。") with "候选 key `valid_stand`、`intensity`、`calories`：后来在 app 活力指标页找到（活动次数 / 活动时长 / 卡路里），三个都对上 → 新 B 域 `activity_daily`。"
  - §6 M7 row: replace "站立小时数、中高强度分钟数、活动卡路里在 app 里没找到对应页面，候选 key 仍未核对" with "三个候选 key 后来在 app 活力指标页对上（活动次数 / 活动时长 / 卡路里）→ `activity_daily`".
- [ ] **Step 4: Catalog prose.**
  - §2, the "**候选**" bullet: replace it with "- **活力指标（2026-10-15 app 已核对）**：app 活力指标页的活动次数 = `valid_stand.count`，活动时长 = `intensity.duration`（分钟），卡路里 = `calories.calories`（kcal）→ B 层 `activity_daily`（`activity_count` / `activity_min` / `active_kcal`）。`calories` 与 `consumption.active_kcal` 同值，两处都保留（[清单 2.8 节](../docs/superpowers/verification/2026-10-15-end-of-run-checklist.md)）。"
  - §5, the sub-bullet "`valid_stand` / `intensity` / `calories` 仍是候选…": replace it with "`valid_stand` / `intensity` / `calories`：后来在 app 活力指标页找到并对上（`app 已核对`），进 B 层 `activity_daily`。"
- [ ] **Step 5: Checklist §2.8.**
  - In the three candidate rows, `结果` becomes `PASS` and `备注` becomes, in order:
    - "app 活力指标页：活动次数（2026-10-15 补核）→ `activity_daily.activity_count`";
    - "app 活力指标页：活动时长，单位分钟（2026-10-15 补核）→ `activity_daily.activity_min`";
    - "app 活力指标页：卡路里，单位 kcal（2026-10-15 补核）→ `activity_daily.active_kcal`".
  - Under "结果与决定（2026-10-15）", add "- 补核（2026-10-15）：2.8 的三个候选 key 在 app 活力指标页找到（活动次数 / 活动时长 / 卡路里），都对上 → 新 B 域 `activity_daily`（[计划](../plans/2026-10-15-mifit-activity-daily.md)）。"
- [ ] **Step 6: Verify.**
  - `python -m pytest -q`: everything passes, including the catalog guard against Unix timestamps.
  - `git diff --check` is clean.
  - Every added line in `git diff -U0 -- README.md CHANGELOG.md docs notes | grep '^+'` holds only labels, names and the date 2026-10-15: no count, minutes or kcal reading.
- [ ] **Step 7: Commit** `git add README.md CHANGELOG.md docs/superpowers/roadmap.md notes/domain-catalog.md docs/superpowers/verification/2026-10-15-end-of-run-checklist.md && git commit -m "docs: activity_daily checked against the app; roadmap, catalog, checklist"`. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Names.** The domain is `activity_daily`, in its own module `domains/activity_daily.py`. `ActivityDailySummary` goes in a new `models/activity.py`, because no existing model covers activity. The facade method is `MiFit.activity_daily`.
2. **Field names:**
   - `activity_count` names what the app shows (活动次数) and claims no unit. `active_hours` was rejected: it reads as a duration, which is `activity_min`'s job, and only one day ties the count to hours.
   - `activity_min` uses the repo's `_min` suffix (`relaxed_min`, `asleep_min`), and it pairs with the profile's `activity_goal_min` (目标活动时间). That the goal is a target for this figure is plausible, but not verified, so no docstring claims it.
   - `active_kcal` reuses the name of the same figure in `ConsumptionRecord` and `Workout`.
   - The fields are ordered as the app page lists them, and `raw` follows the same key order.
3. **`0` is real for all three figures** (`as_non_negative_int`):
   - All three are totals of the day, where `0` means "none that day". HR, SpO2 and stress levels differ: there `0` means "not measured", so they use `as_positive_int`.
   - `active_kcal` must keep a `0` to stay equal to `consumption.active_kcal`, which keeps a reported `0`.
   - `as_int` was rejected because it keeps negatives, which no count or total can be.
   - Floats are truncated: the wire is int, so a float is unexpected.
4. **`active_kcal` is `int | None`.** The daily report sends an int, as `Workout.active_kcal` (`int | None`) does. `ConsumptionRecord.active_kcal` is a float because its endpoint sends floats, and equal values compare equal (`320 == 320.0`).
5. **Three requests, one per key, each through `fetch_daily_summaries`, merged by day.**
   - The per-key read is the verified request shape. A single tag-only read with a time window is not verified (catalog §1).
   - The shared helper already does the decoding, `raw`, the day and the notes per key, so `summarize` only returns a tuple, and the merge (a few lines) stays in the domain.
   - Three requests per call (and per month chunk) are within the low-frequency rule.
6. **`raw` is grouped by key** (one section per key, in service order), not by day. Rows without a usable day have no place in a day order, and grouping by day would need a decode loop of the domain's own. The cost is that a chunked read's merged `raw` holds the same values in another order, as `sleep`'s does. `chunks.py` documents this, and `test_chunks` compares it as a multiset.
7. **Records are sorted newest date first.** The other daily domains get that order from the service (`reverse`). The merge has to sort, so that chunked and single reads give equal `records`.
8. **When one key has two rows for the same day, the first row read wins** (`raw` keeps both). This was never seen live, but the rule makes the result deterministic.
9. **`calories` and `consumption` both stay.** `consumption` costs one request per day and adds resting, intake and total calories. `activity_daily` keeps the app page's three figures together. There is no cross-check note.
10. **`goal` and `process` stay A-only.** The APK declares them but they never appeared live (design §2).
11. **`as_non_negative_int` goes into `base.py` in its own task**, replacing `stress_daily._band_minutes`, which follows the same rule. Two users justify a shared reading, and the existing stress tests guard the swap. A reviewer can reject this step without rejecting the domain.
12. **`PagedFake` gains `by_key`.** All three reads share one endpoint, so routing by endpoint cannot tell them apart. `test_chunks` keeps the unchanged `WindowFake` and gives every key the same rows.
13. **CHANGELOG: the domain goes into 0.2.0, not into an "Unreleased" section.** No tag or release of 0.2.0 exists, `pyproject.toml` already says 0.2.0, and the entry has today's date. An "Unreleased" section would describe code that ships as 0.2.0 as coming after it.
14. **README says "yes".** The app check is already done (2026-10-15, with the user), so the docs land with the code on this branch instead of waiting for a live task. This plan makes no live calls and writes no new verification document: the checklist is the record, as decided on 2026-10-15.
15. **The catalog's three table rows change in Task 2, its prose in Task 3.** The guard test needs every registered domain to be named in a table, while the wording of the record can be reviewed on its own.
16. **No fixture file and no new notes file.** The synthetic rows are written inline (three small envelopes per test), and the catalog already holds the evidence.
17. **Execution:** Native is recommended, following the user's standing preference for small plans: three tasks with one fresh reviewer at the end.

## Self-Review

1. **Spec coverage:**
   - The model with neutral names and app-label docstrings, the domain over steps' day rules through the shared reader, one record for a day in any key, `None` for missing figures, `0` kept, and notes, stall and `raw` as in the other daily domains: Task 2 (with the reading from Task 1).
   - Wiring (client, export, CLI docstring, registry test, chunks with `COMPARISONS`): Task 2.
   - The README row and export list, the CHANGELOG, the roadmap (§0, §1 and its count, §3, §6), the catalog (rows in Task 2, prose in Task 3) and checklist §2.8: Task 3. ✓
2. **Step scan:** every code step names its file, signature and pinned values. Every test step names the test and its assertions. The docs steps give the exact text. ✓
3. **Type consistency:** `ActivityDailySummary(date, epoch_s, activity_count, activity_min, active_kcal)`, `fetch_activity_daily(t, start, end, *, tz)`, `as_non_negative_int(raw)`, `PagedFake(by_key=...)` and the three `what` strings are spelled the same everywhere. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1 or 2. ✓
5. **Proportion:** three tasks for one domain; there are no code bodies, and the only table is the key map. ✓
