# mifit Heart-Rate Daily Summary + Raw-HR Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `heart_rate_daily` domain (one neutral `HeartRateDailySummary` per local day: average, highest, lowest and resting bpm) and close the two raw heart-rate issues: the windowed read that drops points (time-boxed investigation + fix) and the full read's silent 10-page cap.

**Architecture:** The daily summary is one read of the daily report (`get_aggregated_fitness_data_by_time`, `tag=daily_report`, `key=heart_rate`). Its window and dates follow the same rules as steps (`days.report_window` / `report_day` / `local_midnight`). The raw windowed read moves its request start back one day and keeps only rows inside the requested window, the same pattern sleep uses. The full read finds out it hit the page cap through a new `Transport.get_paged_result`, which reports truncation; `get_paged` becomes a thin wrapper around it with the same contract as before.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport`, `domains.days`, `DomainResult`, `register`), pytest. No new deps.

**Spec:** roadmap `docs/superpowers/roadmap.md` (§3 M3, §2 open issues "原始心率按时间窗查询会漏点…/`--full` 10 页上限", §7); design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` (§2 anti-goals, §3 known incompleteness, §8.1, §12); evidence `notes/04-mi-health-lab-cloud-breakthrough.md` §2/§4, `notes/06-daily-report-day-semantics.md`; `docs/superpowers/verification/2026-10-14-core-read-live.md` (heart-rate row); the live findings below.

**Precondition:** T1 is merged into `main`, so `README.md` exists and `notes/07` is taken. Work goes straight onto `main`, or onto a short branch that is fast-forwarded into it.

## Live-verified shapes (UTC+8 account, 2026-10-14, 9 read-only calls; all values below are synthetic)

**Daily report `heart_rate`** (`CloudHrReport`)
- Outer row: the same as every daily report. `{tag:"daily_report", key:"heart_rate", time:<UTC midnight of the local date>, value:<JSON string>, update_time, watermark, source_sid_list, sid}`. There is one row per day that has data, and the current day's row is rewritten during the day.
- Decoded value (every key was present on 6/6 days): `{"avg_hr": 70, "max_hr": 130, "min_hr": 50, "avg_rhr": 60, "abnormal_hr_count": 0, "warm_up_hr_zone_duration": 20, "fat_burning_hr_zone_duration": 3, "aerobic_hr_zone_duration": 0, "anaerobic_hr_zone_duration": 0, "extreme_hr_zone_duration": 0, "latest_hr": {"bpm": 75, "type": 0, "time": 1760486400, "dbKey": "heart_rate", "dbTime": 1760457600, "sid": "synthetic"}}`. The APK class also declares `hr_total` and `hr_time_total`. Upload never sets them, and neither appeared live.
- What the APK says each field means (`CloudDailyHelper.convertHr`, which builds this object for upload; it uploads only when `maxHr > 0`):
  - `avg_hr`/`max_hr`/`min_hr` are `DailyHrReport` avg/max/min.
  - `avg_rhr` is `DailyHrReport.restHr` (nullable).
  - `abnormal_hr_count` is the number of abnormal-HR records that day.
  - The five `*_hr_zone_duration` fields are `DailyHrDistribute` warmUp/fatBurn/aerobic/anaerobic/extreme. The APK states no unit. Live values are small integers, a few tens per day in total, so probably minutes, but that is not verified.
- Already checked against the app on one day: for the core-read verification day, the app's day average, range and resting HR equal that day's `avg_hr`, `min_hr`/`max_hr` and `avg_rhr`.
- In `latest_hr`, `time − dbTime = +28800` on this UTC+8 account. So `time` is the local wall clock written as if it were UTC, and `dbTime` is the true instant (notes/04 §4, "ReportConvertor 平移"). `type` is sometimes absent.

**Raw `resting_heart_rate`**
- At most one row per local day; some days have no row. Outer `{key, sid, time, update_time, value, zone_name, zone_offset}`. Here `time` is the UTC midnight of the local date (like a daily-report key), and `zone_offset` is `null` on older rows. Value: `{"bpm": 60, "date_time": <same as outer time>}`.
- On 6/6 days its `bpm` equaled the daily report's `avg_rhr` for the same date.

**Raw `single_heart_rate`**
- Value `{"bpm": 80, "time": <true epoch s>}` (no `type`); the outer `time` equals the value `time`. Rows come in bursts of about 10–15 samples a few seconds apart, which looks like one spot measurement. Pages hold 20 rows.

**Raw `heart_rate`: the completeness lead**
- Value `{"time", "bpm", "type"}`. The outer `time` equals the value `time` on every row, `type` is `0`, the cadence is mostly 10 minutes, and no two rows share a time. One read with no `start_time` returned a single page of 200+ rows, so the page size is the server's choice.
- **Window starting exactly on a whole UTC hour H** (here the local 08:00): the first row returned was at H+2h. Every row in [H, H+2h) was missing, although all of them are in the no-start read.
  - Same window with `start_time = H − 1 s`: every row was present.
  - With `start_time = H − 1 day`: the rows inside [H, end] were exactly the no-start read's.
  - End side: a row exactly at `end_time` is returned, and no row after it.
- **Reading:** the service picks rows bucket by bucket, with an exclusive start (`start_time < bucket key`, as for daily reports in notes/06). A bucket keyed at or before `start_time` is dropped whole. A bucket covers at least 2 h; this plan assumes at most 1 day. SDK windows start at local midnight, which is a whole UTC hour in most zones, so the first bucket of a day can vanish. That explains notes/04's split-day undercount. Still unchecked, and left to Task 4: a start inside a bucket, other days, an end inside a bucket.
- **How the app reads it (APK):**
  - `CloudSyncLineRouter.pullFullPullFitnessData(key, endTime, nextKey)` sends `GetFitnessDataByTime(key, start=null, end=endTime, reverse=true, nextKey)`. That is the SDK's `fetch_heart_rate_full`.
  - `pullFitnessDataByTime(start, end, nextKey, key)` is the windowed read and uses the class's default `reverse`.

## Global Constraints

- **Read-only and low frequency.** Live calls happen only in Task 4 (12 calls at most) and Task 5, at least 1 s apart (the `Transport` default), and probing stops at the first 401.
- **No secrets or health data in the repo.** Never print, log or commit a token, identifier or health value. Probe scripts and their outputs live in `work/m3/` (git-ignored).
- **Tests stay offline:** synthetic fixtures and a fake transport only. `python -m pytest -q` is green after every task; the baseline is 273 passed, 2 skipped.
- **A + B.** `DomainResult.raw` keeps every decoded `value` dict untouched. B models use neutral names, and no Mi field name or Mi code appears inside `models/`.
- **Anti-goal (design §2): a field whose meaning is not verified stays out of B.** These stay out of B: the five zone durations, `abnormal_hr_count`, `latest_hr`, `hr_total`, `hr_time_total`, and the raw `single_heart_rate` / `resting_heart_rate` keys.
- **Days for `heart_rate_daily` are exactly steps' days:** window `report_window(*local_day_span(start, end, tz))`, `date = report_day(row.time)`, `epoch_s = local_midnight(date, tz)`.
- A bpm of `0`, an absent bpm, a `bool`, or a non-number means "not measured" and becomes `None`. Nothing is ever fabricated.
- **Backward compatible:** the `heart_rate` domain name, `MiFit.heart_rate(start, end, *, full=False)`, `fetch_heart_rate_full(t, end_epoch_s, max_pages=10)`, `HeartRatePoint`, and the signature and return value of `Transport.get_paged` all stay as they are.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Ask the user before any push.

## Review Focus

- **A one-day raw read whose first bucket holds data** (the window starts on a whole hour): every point must be there. → Task 4 tests `test_window_asks_a_day_before_start` and `test_window_keeps_rows_at_both_bounds`.
- **Rows from the extra margin day:** they must not leak into `records` or `raw`, and must not produce a note. → Task 4 `test_window_drops_rows_outside_the_asked_window`.
- **A day without a resting HR** (`avg_rhr` absent, `null` or `0`): `resting_bpm is None`, the other figures are kept, and there is no note. → Task 1 `test_missing_or_zero_figures_become_none`.
- **`--full` that reaches its cap** must say so in `notes`. A full read that ends naturally, or is stopped by the repeated-cursor guard, must not. → Task 3 `test_capped_full_read_says_so`, `test_uncapped_full_read_has_no_cap_note`, and the `get_paged_result` pagination tests.
- **A figure that arrives as a string or a bool** (`"72"`, `true`): it becomes `None`, not a crash or `1`. → Task 1 `test_non_numeric_figures_become_none`.

---

### Task 1: `HeartRateDailySummary` + `heart_rate_daily` domain

**Files:**
- Modify: `src/mifit/models/heart_rate.py` (add the dataclass below `HeartRatePoint`)
- Create: `src/mifit/domains/heart_rate_daily.py`, `tests/test_domain_heart_rate_daily.py`, `tests/fixtures/hr_daily_sample.json`

**Interfaces:**
- Consumes: `Transport.get_paged(endpoint_path, data_obj, *, list_key="data_list", ...) -> list[dict]`; `days.local_day_span`, `days.report_window`, `days.report_day`, `days.local_midnight`; `DomainResult`, `register`.
- Produces:
  - `models/heart_rate.py`: `@dataclass class HeartRateDailySummary(date: str, epoch_s: int, avg_bpm: int | None, max_bpm: int | None, min_bpm: int | None, resting_bpm: int | None)`. Docstring: `date` is the local date and `epoch_s` its local midnight. The four figures are bpm for that day; `resting_bpm` is the day's resting heart rate. Each is `None` when the source did not report it.
  - `domains/heart_rate_daily.py`: `@register("heart_rate_daily") def fetch_heart_rate_daily(t: Transport, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None) -> DomainResult`.
- Request: `get_paged("data/get_aggregated_fitness_data_by_time", {"tag": "daily_report", "key": "heart_rate", "reverse": True, "limit": 50, "start_time": …, "end_time": …}, list_key="data_list")`.
- Field map (wire → B): `avg_hr→avg_bpm`, `max_hr→max_bpm`, `min_hr→min_bpm`, `avg_rhr→resting_bpm`. Each goes through a local `_bpm(raw) -> int | None`: an `int` or `float` (not `bool`) becomes `int(raw)`, then `0` becomes `None`; anything else becomes `None`.
- Row handling follows steps:
  - A `value` that is not valid JSON, or is not an object, is skipped with a note and not added to `raw`.
  - Otherwise the decoded value is appended to `raw` first. A row whose outer `time` is unusable is then skipped with a note.
  - A row with every figure `None` still gives a record.

- [ ] **Step 1: Write the fixture and failing tests.** Fixture: the steps-fixture envelope with two synthetic rows. `DAY_1 = 1760400000` holds every key of the "Live-verified shapes" value. `DAY_2 = DAY_1 + 86400` has no `avg_rhr`. The fake transport is a copy of `test_domain_steps.FakeTransport`, and there is a `payload_with_rows((time, value), ...)` helper. Tests:
  - `test_heart_rate_daily_domain_is_registered`: `REGISTRY["heart_rate_daily"] is fetch_heart_rate_daily`.
  - `test_requests_daily_report_heart_rate_for_the_window`: with `tz=UTC`, `START = DAY_1` and `END = DAY_2 + 86399`, the one call's `data_obj == {"tag": "daily_report", "key": "heart_rate", "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}` on the aggregated endpoint.
  - `test_maps_day_figures`: `records[0] == HeartRateDailySummary(date="2025-10-14", epoch_s=DAY_1, avg_bpm=70, max_bpm=130, min_bpm=50, resting_bpm=60)`, and `notes == []`.
  - `test_epoch_is_the_local_midnight_of_the_report_date`: with `tz=timezone(timedelta(hours=8))` and the matching local `START`/`END`, `records[0].date == "2025-10-14"` and `records[0].epoch_s == DAY_1 - 28800`.
  - `test_missing_or_zero_figures_become_none`: rows where `avg_rhr` is absent, `null` or `0`, and where `max_hr` is `0`, give `None` for that field only; the other fields keep their wire values; `notes == []`.
  - `test_non_numeric_figures_become_none`: `"72"`, `true` and `[70]` all give `None`, with no exception.
  - `test_raw_keeps_decoded_values_untouched`: `result.raw` equals the two decoded fixture values, zones and `latest_hr` included.
  - `test_row_with_bad_value_is_skipped_with_note`: a value of `"not json"` and a value of `"[1]"` give no record and one note each, and neither appears in `raw`.
  - `test_row_without_usable_day_is_skipped_with_note`: an outer `time` that is missing, or is `"x"`, gives no record and one note; its decoded value is still in `raw`.
  - `test_unverified_fields_stay_out_of_the_summary`: `{f.name for f in dataclasses.fields(HeartRateDailySummary)} == {"date", "epoch_s", "avg_bpm", "max_bpm", "min_bpm", "resting_bpm"}`.
  - `test_empty_window_yields_no_records_and_no_notes`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_heart_rate_daily.py -v`. Expected: FAIL (the module is missing).
- [ ] **Step 3: Implement** the dataclass and `fetch_heart_rate_daily`. The module docstring says:
  - the day rules are those of steps (notes/06);
  - `resting_bpm` comes from `avg_rhr`, which the raw `resting_heart_rate` key repeats;
  - zones, `abnormal_hr_count` and `latest_hr` stay in `raw` because their meaning is unverified, and `latest_hr.time` is a shifted local clock.
- [ ] **Step 4: Run** the file and then `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/models/heart_rate.py src/mifit/domains/heart_rate_daily.py tests/test_domain_heart_rate_daily.py tests/fixtures/hr_daily_sample.json && git commit -m "feat(domains): heart-rate daily report -> HeartRateDailySummary"`

---

### Task 2: Facade, CLI and package export

**Files:**
- Modify: `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` (the domain list in the module docstring only), `tests/test_client.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `fetch_heart_rate_daily` and `HeartRateDailySummary` from Task 1.
- Produces:
  - `MiFit.heart_rate_daily(self, start: DateArg, end: DateArg) -> DomainResult`, which returns `fetch_heart_rate_daily(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Docstring: one record per local day with data, giving the day's average, highest, lowest and resting heart rate as the app shows them.
  - `mifit.HeartRateDailySummary`, exported in `__all__`.
  - `mifit export heart_rate_daily`. The CLI dispatches through `REGISTRY` and `getattr`, so this needs no CLI code; `--full` triggers the existing "ignored" note.

- [ ] **Step 1: Write failing tests:**
  - `test_client.py`:
    - `test_heart_rate_daily_delegates_to_domain_with_local_day_bounds`: a spy on `client_module.fetch_heart_rate_daily`, exactly like `test_sleep_delegates_to_domain_with_local_day_bounds`.
    - `test_importing_client_registers_the_domains`: the expected value becomes `"['consumption', 'heart_rate', 'heart_rate_daily', 'sleep', 'steps']"`.
    - `test_heart_rate_daily_is_registered_once_client_is_imported`.
    - `test_package_exports`: add `mifit.HeartRateDailySummary is HeartRateDailySummary`.
  - `test_cli.py`:
    - Add `HR_DAY = HeartRateDailySummary(date="2026-10-01", epoch_s=1790812800, avg_bpm=70, max_bpm=130, min_bpm=50, resting_bpm=None)`, and give `FakeMiFit.heart_rate_daily(start, end)` a return of `DomainResult(records=[HR_DAY], raw=[{"avg_hr": 70}], notes=[])`.
    - `test_export_heart_rate_daily_writes_records`: `records == [asdict(HR_DAY)]`, and the calls are `[("heart_rate_daily", "2026-10-01", "2026-10-02", {})]`.
    - `test_export_full_flag_is_ignored_for_heart_rate_daily`: exit code 0, `"--full"` appears in stderr, and the call carries no `full`.
- [ ] **Step 2: Run** `python -m pytest tests/test_client.py tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the facade method, the domain import in `client.py` (that import is what registers the domain), the package export, and add `heart rate daily` to the CLI docstring's domain list.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. Then `python -m pip install -e . && mifit export --help` should list `heart_rate_daily`.
- [ ] **Step 5: Commit** `git commit -m "feat(client,cli): expose heart_rate_daily"`. The README stays unchanged until Task 5, so it never claims an unverified domain.

---

### Task 3: Tell the caller when the full read hits its page cap

**Files:**
- Modify: `src/mifit/transport.py`, `src/mifit/domains/heart_rate.py` (`fetch_heart_rate_full` only), `tests/test_pagination.py`, `tests/test_domain_heart_rate.py`, `tests/test_client.py` (fake only), `README.md` (the `--full` sentence)

**Interfaces:**
- Produces in `transport.py`:

```python
@dataclass(frozen=True)
class PagedResult:
    items: list[dict]
    truncated: bool  # stopped by max_pages although the service had more pages

def get_paged_result(self, endpoint_path: str, data_obj: dict, *, list_key: str = "data_list",
                     sign_path: str | None = None, max_pages: int | None = None) -> PagedResult: ...
```

  - The current `get_paged` body moves into `get_paged_result`. `truncated` is `True` only when the loop ends because `pages == max_pages` while the last page had a truthy `has_more` and a non-empty `next_key` different from the request's cursor, i.e. when another request would have been sent. With `max_pages <= 0` it is `False`.
  - `get_paged(...)` keeps its signature and docstring contract and returns `self.get_paged_result(...).items`.
- `fetch_heart_rate_full` calls `t.get_paged_result(...)` with the same arguments as today. When `truncated` is true it appends `f"Full heart-rate read stopped at its {max_pages}-page cap while the service still had older points: points before the earliest one returned are missing."`

- [ ] **Step 1: Write failing tests:**
  - `test_pagination.py`, reusing `ScriptedTransport` and `page()`:
    - `test_result_reports_truncation_at_the_page_cap`: scripted pages `[page(A, has_more=True, next_key="k1"), page(B, has_more=True, next_key="k2")]` with `max_pages=2` give `truncated is True`, `items == A + B`, and 2 calls.
    - `test_result_not_truncated_when_the_last_page_ends`: the second page has `has_more=False`, so `truncated` is `False`.
    - `test_result_not_truncated_when_the_cursor_repeats`: the second page repeats `next_key="k1"` with `has_more=True` and `max_pages=2`, so `truncated` is `False`.
    - `test_result_not_truncated_without_a_cap`: `max_pages=None` gives `False`.
    - `test_get_paged_returns_the_result_items`.
  - `test_domain_heart_rate.py`:
    - `FakeTransport.__init__(self, payload, truncated=False)` gains a `get_paged_result` override. It records into `self.calls` exactly like `get_paged` and returns `PagedResult(<same list>, self._truncated)`.
    - `test_capped_full_read_says_so`: with `truncated=True` there is exactly one note, and it contains `"10-page cap"`.
    - `test_uncapped_full_read_has_no_cap_note`: `notes == []`.
  - `test_client.py`: `FakeTransport` gets the same `get_paged_result` override (returning `truncated=False`), so `test_heart_rate_full_pages_back_from_end_without_start` stays unchanged and passes.
- [ ] **Step 2: Run** `python -m pytest tests/test_pagination.py tests/test_domain_heart_rate.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `PagedResult`, `get_paged_result`, the `get_paged` wrapper and the cap note. In the README's Known limitations, replace "and stops after 10 pages without a warning" with "and stops after 10 pages, saying so in `notes` when older data remains".
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. The existing pagination tests are untouched and still pass.
- [ ] **Step 5: Commit** `git commit -m "feat(transport,heart_rate): report when the full read stops at its page cap"`

---

### Task 4: Raw-HR window completeness (time-boxed investigation + fix)

**Time box:** one work unit and at most 12 live calls. When either runs out, stop and write down what is known.

**Files:**
- Create: `notes/08-heart-rate-keys-and-completeness.md` (Chinese, like notes/01–07); `work/m3/probe_hr_window.py` and its outputs (git-ignored, never committed)
- Modify: `src/mifit/domains/heart_rate.py`, `tests/test_domain_heart_rate.py`, `tests/test_client.py` (`test_heart_rate_window_delegates_to_domain` start assertion), `src/mifit/client.py` (docstrings of `heart_rate` and `raw`), `README.md` (the raw-HR known limitation)

**Interfaces:**
- Consumes: `fetch_heart_rate_window(t, start_epoch_s, end_epoch_s) -> DomainResult` (same signature) and `_build_result(entries)` (unchanged).
- Produces:
  - `_START_MARGIN_S = 86400`. `fetch_heart_rate_window` requests `{"key": "heart_rate", "start_time": start_epoch_s - _START_MARGIN_S, "end_time": end_epoch_s, "reverse": True}` and passes on to `_build_result` only the entries whose outer `time` satisfies `start_epoch_s <= int(time) <= end_epoch_s`.
  - An entry whose outer `time` is missing or not an integer is dropped with the note `"Skipped a heart-rate row without a usable time."` and is not added to `raw`.
  - Margin rows are dropped silently.

- [ ] **Step 1: Probe (live, read-only).** Write `work/m3/probe_hr_window.py`:
  - **How it calls the service:** `Transport(load_session(), delay_s=2.0).get_json(...)`, paging by hand. It saves every answer to `work/m3/<tag>.json` and prints only the tag, the row count, `has_more`, and the first and last row's local clock time (no bpm). It stops on `AuthError`. Comparison is offline, from the saved files.
  - **Q3 (first):** pick 3 recent complete local days D1–D3 with data.
    - One no-start read: `end_time = local_midnight(D3+1) − 1`, paged until it passes `local_midnight(D1)`, at most 4 calls.
    - For each day, the old window `[local_midnight(D), local_midnight(D+1) − 1]` (3 calls).
    - For each day, the widened window `[local_midnight(D) − 86400, same end]` (3 calls).
  - **Q1:** a window `[H+30 min, H+6 h]`, where H is a whole UTC hour with no-start rows in [H, H+2h) (1 call). Are the rows of [H+30 min, next bucket) missing?
  - **Q2:** a window `[E − 1 day, E]`, where E is a whole hour + 30 min inside a data run (1 call). Are all no-start rows ≤ E present?
- [ ] **Step 2: Decide the gate** from the offline comparison (record it in notes/08):
  - **G-pass:** on every compared day, the widened window's rows within that day equal the no-start rows within that day.
  - **G-fail:** any difference, or the probe could not run (401, no session, time box spent).
  - **End side:** if Q2 shows rows ≤ E missing, also request `end_time = end_epoch_s + _START_MARGIN_S` (the same filter applies), and add `test_window_asks_a_day_after_end` in Step 3.
- [ ] **Step 3: Write failing tests** in `test_domain_heart_rate.py`. Add a `payload_with_rows((outer_time, value), ...)` helper.
  - `test_window_asks_a_day_before_start` replaces `test_window_requests_heart_rate_for_the_window`: `data_obj == {"key": "heart_rate", "start_time": START - 86400, "end_time": END, "reverse": True}`.
  - `test_window_drops_rows_outside_the_asked_window`: rows at outer times `START - 1` and `END + 1` are in neither `records` nor `raw`, and no note mentions them.
  - `test_window_keeps_rows_at_both_bounds`: rows at outer times `START` and `END` give two records.
  - `test_row_without_usable_time_is_skipped_with_note`: an outer `time` that is absent, or is `"x"`, gives no record, no `raw` entry, and exactly one note containing `"usable time"` per row.
  - **On G-pass:** `test_window_result_flags_possible_incompleteness` becomes `test_window_result_has_no_incompleteness_note` (`notes == []` on the fixture), and `test_empty_window_yields_no_records` asserts `notes == []`. In `test_entry_without_bpm_is_skipped_not_zeroed`, the comment "beside the incompleteness note" is dropped.
  - **On G-fail:** keep those two tests as they are.
  - `test_client.py::test_heart_rate_window_delegates_to_domain` asserts `start_time == 1759248000 - 86400`.
- [ ] **Step 4: Run** `python -m pytest tests/test_domain_heart_rate.py tests/test_client.py -q`. Expected: the new and changed tests FAIL.
- [ ] **Step 5: Implement** the margin and the filter. They are unconditional: the fix only ever adds rows that lie inside the window.
  - **On G-pass**, delete `_WINDOW_INCOMPLETE_NOTE`.
  - **On G-fail**, keep the note but change its wording to: "may be incomplete: the service drops rows near a window's start; this read asks a day earlier, which recovered them in tests but is not proven for every case (notes/08)".
  - Rewrite the module docstring: the bucket rule, the margin, a pointer to notes/08, and that the full read remains for paging back without a start.
  - `MiFit.heart_rate`: drop "flagged as possibly incomplete" on G-pass.
  - `MiFit.raw`: add one sentence: "This is the service's own answer for the window; for bucketed keys such as `heart_rate` it omits rows of a bucket keyed at or before `start` (notes/08), so widen `start` and filter yourself if you need every row."
- [ ] **Step 6: Write `notes/08-heart-rate-keys-and-completeness.md`**, using synthetic values and no dates or values from the account:
  - §1 心率相关 key 与形状: daily `heart_rate`, raw `heart_rate` / `resting_heart_rate` / `single_heart_rate`, taken from this plan's "Live-verified shapes", plus the APK meanings.
  - §2 窗口漏点：现象、实验、结论: the lead plus the Step 1 results as counts and clock offsets.
  - §3 SDK 处理: margin + filter, and the gate outcome.
  - §4 未决: the exact bucket size and alignment; whether other raw keys behave the same (`MiFit.raw` is left unfiltered); and that the app itself pulls without a start (`pullFullPullFitnessData`).
  - README Known limitations, rewritten from the gate outcome:
    - **G-pass:** "The windowed heart-rate read asks the service for a day before the start and keeps only the requested window, because the service drops samples at a window's start (notes/08)."
    - **G-fail:** keep "may be incomplete" and name the known part of the cause.
- [ ] **Step 7: Run** `python -m pytest -q`. Expected: everything passes, and `git status` shows nothing under `work/`.
- [ ] **Step 8: Commit** `git add notes/08-heart-rate-keys-and-completeness.md src/mifit/domains/heart_rate.py src/mifit/client.py tests/test_domain_heart_rate.py tests/test_client.py README.md && git commit -m "fix(heart_rate): window read asks a day earlier and filters (service drops a window's first bucket)"`

---

### Task 5: Live verification (manual, needs the user; not CI) + roadmap

**Files:**
- Create: `docs/superpowers/verification/<run date>-hr-daily-live.md`
- Modify: `docs/superpowers/roadmap.md` (§0 M3 status, §1 domain list, §2 raw-HR row, §3 M3 `结果` line); `README.md` (add the `heart_rate_daily` row to "What it reads today" and add it to the export `<domain>` list, only for fields that pass)

- [ ] **Step 1:** Run two exports, both writing outside the repo:
  - `mifit export heart_rate_daily --from <D-3> --to <D-1> --json "$HOME\mifit-verify\hr_daily.json"`
  - `mifit export heart_rate --from <D-1> --to <D-1> --json "$HOME\mifit-verify\hr_window.json"`
- [ ] **Step 2:** For the 3 complete days, the user reads from the app's heart-rate page: the day's average, highest, lowest and resting heart rate. For later work only, they also note whether the app shows HR-zone minutes or abnormal-HR alerts for those days.
- [ ] **Step 3:** Compare.
  - Check `avg_bpm` / `max_bpm` / `min_bpm` / `resting_bpm` against the app per day. A field that does not match is removed from B (it stays in `raw`) or renamed to what it really is; record the decision.
  - For D-1, check that the windowed points' min / max / mean agree with the app's day range and average, and with `heart_rate_daily` for that day.
- [ ] **Step 4:** Record pass or partial per field in the verification doc (match/differ only, with no tokens and no values beyond what a row needs). Then:
  - Update the roadmap: M3 ✅; the §2 raw-HR row struck through and marked "已定位/已修", or "已知限制" per the Task 4 gate; the `--full` cap marked done.
  - Update the README domain table.
  - `python -m pytest -q`, then commit `docs: heart-rate daily live verification; roadmap M3`. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Name:** the domain is `heart_rate_daily`. The function `fetch_heart_rate_daily` lives in its own module, `domains/heart_rate_daily.py`, next to the raw-points `heart_rate.py`. `HeartRateDailySummary` goes into `models/heart_rate.py`, beside `HeartRatePoint`. The facade method is `MiFit.heart_rate_daily`. `heart_rate` keeps its name and behavior.
2. **B fields are avg / max / min / resting bpm only.** They have a clear APK meaning, and the app already matched them on one day. Zones (no unit, user-specific thresholds), `abnormal_hr_count` (only zeros were seen, so it cannot be checked), `latest_hr` (its time is a shifted local clock) and `hr_total`/`hr_time_total` (absent) stay A-only. Task 5 records what the app shows for zones and alerts so a later change can promote them.
3. **`resting_bpm` comes from the daily report's `avg_rhr`**, not from raw `resting_heart_rate`. The two carry the same figure (6/6 days), this costs one read instead of two, and the daily report's day semantics are already settled (notes/06).
4. **Raw `resting_heart_rate` and `single_heart_rate` stay A-layer** via `MiFit.raw(key, …)`, and they get no domains this milestone. Resting duplicates a B field. Single is bursts from spot measurements whose presentation in the app is unverified. Their shapes go into notes/08, and `HeartRatePoint` would fit `single_heart_rate` later at near-zero cost.
5. **`0` bpm becomes `None`** for all four figures, as sleep does for heart rate and SpO2.
6. **The window fix widens the start by a day and filters on the outer `time`**, not by a 1-second nudge: a start inside a bucket presumably loses rows too, and Task 4 Q1 checks this. The cost is about one extra day of rows per read. The fix is unconditional; only removing the caveat note depends on the Task 4 gate.
7. **`MiFit.raw` stays the service's unfiltered answer** (A layer, as its contract says). It gets a docstring warning instead of a hidden widening.
8. **Cap detection uses a new `Transport.get_paged_result -> PagedResult(items, truncated)`.** `get_paged` is unchanged for every caller and fake. There is no CLI flag to raise the cap: long ranges, chunking and resuming belong to T3. The note reaches the CLI user through the existing `note:` stderr lines.
9. **The full read stays as it is** (backward compatible), even if the window fix makes it unnecessary for completeness.
10. **One notes file, `notes/08`,** holds both the HR key catalog and the investigation, because notes/07 is taken by T1's history audit.
11. **The roadmap's sleep-HR target is already met by M2b** (`SleepRecord.avg_hr_bpm` was checked against the app), so it is not redone here.
12. **Task order:** the daily summary comes first because it does not need a live session; the investigation comes fourth so an expired session cannot block the code tasks. Task 4's probe stays within the design's low-frequency rule (12 calls at most, 2 s apart).
13. **README:** the new domain is listed only after Task 5 verifies it (following the T1 rule "do not over-claim verification"). Limitation wording changes in the same task as the code that changes the behavior.

## Self-Review

1. **Spec coverage**, against roadmap M3:
   - The daily `heart_rate` → `HeartRateDailySummary` (avg/max/min/resting) is Task 1, exposed through `MiFit` and `mifit export` in Task 2.
   - Zones and abnormal count are A-only by decision 2.
   - Resting and single raw keys are covered by decision 4, with notes/08 §1.
   - The time-boxed completeness investigation, with notes/08 and a fix or a written known limitation, is Task 4.
   - The `--full` cap warning is Task 3.
   - Completion criteria (resting, average, highest and lowest match the app; completeness located or documented) are Task 5 and the Task 4 gate.
   - Roadmap §2 rows are updated in Task 5. ✓
2. **Step scan:** every code step names its file, signature and pinned values. The two conditional points in Task 4 (G-pass or G-fail, and the end side) each spell out the exact change for both outcomes. ✓
3. **Type consistency:** `HeartRateDailySummary(date, epoch_s, avg_bpm, max_bpm, min_bpm, resting_bpm)`, `fetch_heart_rate_daily(t, start, end, *, tz)`, `PagedResult(items, truncated)`, `get_paged_result(...)` and `_START_MARGIN_S` are named the same in every task. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1, 3 or 4. ✓
5. **Proportion:** five tasks, one domain and two small fixes; the only code block is the transport signature. ✓
