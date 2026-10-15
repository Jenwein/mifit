# mifit Post-Verification Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the results of the 2026-10-15 live check: adjust four B models to what the app showed, carry out the user's public-repo cleanup decisions, and move the status documents from "pending" to "verified".

**Architecture:** Task 1 changes the B layer only. `Workout` gains `total_kcal`. `WorkoutSample` keeps a cadence of `0` and loses `altitude_m`. `StressDailySummary` gains four band-minute fields. `UserProfile` gains `activity_goal_min`. Each change is a mapping edit in the domain plus the field on the model, and `raw` stays untouched. Task 2 edits only the current versions of tests and documents: real readings become synthetic values or "一致 / 不一致" wording, and git history is not rewritten. Task 3 updates the README, the roadmap, the notes and the catalog to the verified state, and points to the checklist as the verification record.

**Tech Stack:** Python 3.12+, existing mifit modules (`domains.base` helpers `as_int` / `as_float` / `as_positive_int`, `fetch_daily_summaries`, `to_samples`, `to_workout`, `fetch_profile`), pytest. No new deps.

**Spec:** `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` (§1–§2 result tables, §3 decisions, §4 "核对完成后的收尾", final section "结果与决定"). Task 2 also uses `notes/07-history-audit.md` §3 (the list of files and figures) and §5 (the options). Design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` §2 (anti-goal: only verified fields in B).

**Precondition:** branch `post-verification`, created from `main` at `0ca71bc`. `python -m pytest -q` gives 1195 passed, 2 skipped.

## Verified on 2026-10-15 (the facts this plan relies on; no figures)

- **workouts:** the app shows both energy figures for a workout. `calories` is the 活动卡路里 and `total_cal` the 总卡路里. The app's duration excludes pauses and equals `duration_s`.
- **workout_detail:**
  - The app's average cadence counts the seconds with cadence 0, and the per-second values are right.
  - Altitude: the ride is within about 0.1 m of the app, but the run's extremes are 2–3 m off, which fails the 3 m rule.
  - When the bike stands still, the app's speed chart shows 0 (Q6).
- **stress_daily:** the levels match the app. The `stress_scale` keys `relax` / `mild` / `moderate` / `severe` are minutes: they match the app's durations and shares.
- **profile:** the `regular_goal_list` entry with `field` 4 is the app's 目标活动时间, in minutes. Fields 1 and 2 repeat `daily_step_goal` / `daily_cal_goal` (notes/11 §3). The sleep goal was not found in the app.
- **T2:**
  - A session saved before T2 was still readable about 22 h after its login.
  - QR login with deviceId works.
  - A forced 401 renewed the session (`created_at` unchanged), and the next export renewed nothing.
- **Not checked:**
  - the candidate keys `valid_stand` / `intensity` / `calories` (their app pages were not found);
  - pause positions;
  - HR zones and alerts (the app does not show them);
  - body composition (no weight data source);
  - the non-cn FDS host (Q8);
  - Q1, Q3, Q5, Q7, and Q2's zero-bpm gap.

## Global Constraints

- **No health figures, ids or account facts in any committed file.** This plan is included.
  - New wording never introduces a figure. A reading becomes "一致 / 不一致" (English docs: "match" / "differ") or synthetic wording.
  - Tests use synthetic values only.
  - The figure lists used for checking live in `work/post-verification/` (git-ignored). They are never committed and never quoted in a commit message.
- **Offline only.** No live call anywhere in this plan; tests use fakes and synthetic fixtures. `python -m pytest -q` is green after every commit:
  - baseline: 1195 passed, 2 skipped;
  - after Task 1: 1213 passed, 2 skipped;
  - Tasks 2 and 3 add no tests.
- **A + B.** Every domain keeps `DomainResult.raw` exactly as decoded.
  - B models use neutral names. No Mi field name (`total_cal`, `stress_scale`, `regular_goal_list`, `cadence`, …) appears in `models/`, including their docstrings.
  - Existing tests change only where the B model changed.
- **Anti-goal (design §2):** only meanings verified on 2026-10-15 enter B, and a field that differed from the app (`altitude_m`) leaves B.
- **Zero rules:**
  - `total_kcal` and `activity_goal_min`: `0`, absent, negative or not a number → `None` (`as_positive_int`, like their neighbours).
  - Band minutes and run cadence: `0` is a real value and stays `0`; absent or unusable → `None`.
- **No history rewrite, no force push, no push** (checklist §3: "不改写历史"). Integration happens after the final review.
- **Region and timezone stay** (cn, UTC+8 / Asia/Shanghai). Only the phone model is removed.
- **Line endings:** LF. Python `write_text` writes CRLF on this machine, so write with `newline="\n"` or normalize. `git ls-files --eol` must show `i/lf w/lf` for every touched file.
- **Commits** go on `post-verification` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **A stress row without a usable `stress_scale`** (absent, `null`, a list, a string; older rows): the three levels still map, the four band fields are `None`, and there is no note or crash. → Task 1 Part C `test_band_minutes_are_none_without_a_scale_object`.
- **A band of `0` minutes** (a calm day's `severe: 0`) must give `0`, not `None`. A negative, string, bool or NaN band gives `None`. → Part C `test_second_day_without_lowest_figure_keeps_the_rest`, `test_band_minutes_keep_zero_and_drop_unusable_values`.
- **A goal list in an unusual shape**:
  - no field-4 entry;
  - not a list;
  - non-object entries;
  - two field-4 entries;
  - entries that disagree with `daily_step_goal` / `daily_cal_goal`.

  The result is never a crash, and never a value taken from a field-1 or field-2 entry. → Part D `test_activity_goal_is_the_first_field_4_target`, `test_activity_goal_is_none_when_absent_or_unusable`, `test_step_and_calorie_goals_ignore_the_goal_list`.
- **A run second with cadence `0` next to a second without cadence**: `0` stays `0` and absent stays `None`. The ride's cadence byte is never mapped. → Part B `test_cadence_zero_is_kept_pace_zero_becomes_none_both_run_only`.
- **A workout reporting only one of its two energy figures**: each maps on its own. → Part A, the added cases in `test_absent_or_zero_figures_become_none`.

---

### Task 1: B-layer adjustments from the live results

Four parts, one commit each. Every part follows TDD: write the failing tests, then implement and update the docs that describe that B field.

**Files:**
- Part A: `src/mifit/models/workouts.py`, `src/mifit/domains/workouts.py`, `tests/test_domain_workouts.py`
- Part B: `src/mifit/models/workout_detail.py`, `src/mifit/domains/workout_detail.py`, `tests/test_workout_detail_mapping.py`, `tests/test_domain_workout_detail.py`
- Part C: `src/mifit/models/stress.py`, `src/mifit/domains/stress_daily.py`, `tests/test_domain_stress_daily.py`
- Part D: `src/mifit/models/profile.py`, `src/mifit/domains/profile.py`, `tests/test_domain_profile.py`
- Every part also touches:
  - `tests/test_cli.py`: the module constants `WORKOUT`, `DETAIL`, `STRESS_DAY`, `PROFILE`;
  - `src/mifit/client.py`: the docstrings of `workouts`, `workout_detail`, `stress_daily`, `profile`;
  - `src/mifit/cli/__init__.py`: the module docstring;
  - `README.md`: the "What you get" cell of the four rows;
  - the notes lines describing that field's B/A split: notes/10 §5.1–5.2, notes/12 §8.2–8.3, notes/11 §3 and §5, and the `stress_scale` bullet in notes/domain-catalog.md §5.

**Interfaces:**
- Consumes: `as_int`, `as_float`, `as_positive_int` (`domains/base.py`) and `fetch_daily_summaries(..., summarize=_summary)` (`domains/daily_report.py`).
- Produces (later tasks name these exactly):
  - `Workout(start_epoch_s, end_epoch_s, utc_offset_s, sport, source_sport_code, duration_s, distance_m, active_kcal, total_kcal, avg_hr_bpm, max_hr_bpm, steps)`, where `total_kcal: int | None`.
  - `WorkoutSample(epoch_s, heart_rate_bpm, distance_m, steps, cadence_spm, pace_s_per_km, speed_mps)`, with no altitude.
  - `StressDailySummary(date, epoch_s, avg_stress_level, max_stress_level, min_stress_level, relaxed_min, mild_min, moderate_min, severe_min)`, where each new field is `int | None`.
  - `UserProfile(height_cm, weight_kg, step_goal, calorie_goal_kcal, activity_goal_min)`, where `activity_goal_min: int | None`.
  - Private helpers: `stress_daily._band_minutes(raw: object) -> int | None` and `profile._activity_goal(goals: object) -> int | None`.
  - None of the new fields has a default (like every other field).

#### Part A: `Workout.total_kcal`

- [ ] **A1: Change the tests.** In `tests/test_domain_workouts.py`:
  - `expected_run` gains `total_kcal=470`.
  - `test_maps_core_fields`: strength `total_kcal=180`, run `total_kcal=470` (the fixture's `total_cal`).
  - `test_absent_or_zero_figures_become_none` gains these cases:
    - `(run_value(total_cal=0), {"total_kcal": None})`
    - `(run_value(total_cal=ABSENT), {"total_kcal": None})`
    - `(run_value(calories=ABSENT), {"active_kcal": None})`
  - `test_non_numeric_figures_become_none`: `"total_cal"` joins `figures`, and `nothing_reported` gains `total_kcal=None`.
  - `test_raw_keeps_decoded_values_untouched`: drop `"total_cal"` from the tuple of left-out keys. It is mapped now, and `raw == [STRENGTH_VALUE, RUN_VALUE]` still covers it.
  - `test_unverified_fields_stay_out_of_workout`: the set gains `"total_kcal"`.
  - In `tests/test_cli.py`, `WORKOUT` gains `total_kcal=410`.
- [ ] **A2: Run** `python -m pytest tests/test_domain_workouts.py tests/test_cli.py -q`. Expected: FAIL (`TypeError` on `total_kcal`).
- [ ] **A3: Implement.**
  - Add `total_kcal: int | None` right after `active_kcal`.
  - In `to_workout`, set `total_kcal=as_positive_int(value.get("total_cal"))`.
  - Docstrings:
    - model: `active_kcal` is the activity energy without resting energy; `total_kcal` adds the resting energy burned during the workout; the app shows both, as active and total calories;
    - domain module: take "the total energy (…still to check)" out of the raw-only list, and say both energy figures were checked against the app (2026-10-15);
    - `MiFit.workouts`: "active and total calories".
  - README `workouts` cell: "active and total calories".
  - notes/10 §5.1: add the row `| \`total_cal\` | \`total_kcal\` | 同上 |` after the `calories` row. In §5.2, drop `total_cal` from the list.
- [ ] **A4: Run** `python -m pytest -q`. Expected: 1195 passed, 2 skipped.
- [ ] **A5: Commit** `git commit -am "feat(workouts): add Workout.total_kcal from total_cal (checked against the app)"`

#### Part B: `WorkoutSample`: keep cadence 0, move altitude out of B

- [ ] **B1: Change the tests.**
  - In `tests/test_workout_detail_mapping.py`:
    - Rename `test_cadence_and_pace_zero_become_none_and_are_run_only` to `test_cadence_zero_is_kept_pace_zero_becomes_none_both_run_only`. On the same three points: run cadence `[170, 0, None]`, run pace `[360, None, None]`, ride cadence and pace `[None, None, None]`.
    - Delete `test_altitude_accumulates_from_each_segment_start`, `test_altitude_without_a_valid_change_is_none_and_not_accumulated` and `test_non_finite_initial_altitude_gives_none_for_the_segment`.
    - `test_whole_samples_of_a_run_and_a_ride` expects `WorkoutSample(T, 150, 2.5, 3, 172, 355, None)` and `WorkoutSample(T, 130, 6.0, None, None, None, 6.0)`.
    - `test_b_models_hold_only_verified_fields`: the list loses `"altitude_m"`.
  - In `tests/test_domain_workout_detail.py`, `test_maps_a_run_and_a_ride` adds these assertions, which show the A layer keeps the altitude:
    - with `seg = result.raw[0]["record"]["segments"][0]`, `seg["initialAltitude"] == 50.0`;
    - `seg["dataPoints"][0]["isAltitudeRising"] == 1` and `seg["dataPoints"][0]["altitudeChange"] == 5`;
    - `not hasattr(run.samples[0], "altitude_m")`.
  - In `tests/test_cli.py`: `DETAIL` samples become `[WorkoutSample(1790820000, 120, 2.5, 3, 170, 360, None)]`.
- [ ] **B2: Run** `python -m pytest tests/test_workout_detail_mapping.py tests/test_domain_workout_detail.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **B3: Implement.**
  - Remove `altitude_m` from `WorkoutSample`.
  - In `to_samples`, set `cadence_spm=point.get("cadence") if run else None` (the A layer has no key for an invalid value).
  - Delete the altitude running sum and the `initialAltitude` read, and the `as_float` import that is then unused.
  - Docstrings:
    - model: `cadence_spm` `0` is a real reading (the app's average counts those seconds); `speed_mps` `0.0` is a real standstill (the app charts 0);
    - domain module, cadence rule: `0` kept;
    - domain module, speed rule: it cites the live answer;
    - domain module, altitude: the `altitude_m` bullet becomes a raw-only bullet (`initialAltitude` and the per-second `isAltitudeRising` / `altitudeChange`). The curve rebuilt by the APK rule matched the app for the ride, but the run's extremes were 2–3 m off the app's, so it is not in B (spec §16 Q4). It replaces the bullet "the raw altitude bits: `altitude_m` is built from them".
  - `MiFit.workout_detail` docstring and README `workout_detail` cell: drop "altitude".
  - notes/12:
    - §8.2 cadence row: "只在协议 22；`0` 是真实读数（app 的平均步频把步频为 0 的秒算在内），保留；无效 → `None`".
    - §8.2 speed row: cite "app 的速度图停车时为 0".
    - §8.2: delete the `altitude_m` row.
    - §8.3: replace the bullet on the raw altitude bits with one stating that the altitude (`initialAltitude` + `isAltitudeRising` / `altitudeChange`) is A-only, and why (2026-10-15, §10 Q4).
- [ ] **B4: Run** `python -m pytest -q`. Expected: 1190 passed, 2 skipped, which is −5 (the deleted tests, one parametrized ×3). Then `git grep -n "altitude_m" -- src tests README.md` prints nothing.
- [ ] **B5: Commit** `git commit -am "fix(workout_detail): keep cadence 0; altitude leaves WorkoutSample (raw only)"`

#### Part C: `StressDailySummary` band minutes

- [ ] **C1: Change and add tests.** In `tests/test_domain_stress_daily.py`, add a helper `bands(record)` that returns `(relaxed_min, mild_min, moderate_min, severe_min)`. Then:
  - `test_maps_day_figures` adds `relaxed_min=600, mild_min=300, moderate_min=60, severe_min=5`.
  - `test_second_day_without_lowest_figure_keeps_the_rest` adds `relaxed_min=700, mild_min=200, moderate_min=30, severe_min=0`. The fixture's `severe: 0` stays `0`.
  - `test_row_with_every_figure_missing_still_gives_a_record`: the value becomes `{"stress_scale": {}}`, and every one of the seven figures is `None`.
  - `test_unverified_fields_stay_out_of_the_summary`: the set gains the four names.
  - New `test_band_minutes_keep_zero_and_drop_unusable_values`:
    - rows `(DAY_1, figures(stress_scale={"relax": 0, "mild": 45.9, "moderate": "30", "severe": True}))` and `(DAY_2, figures(stress_scale={"relax": -5, "mild": float("nan"), "moderate": None}))`;
    - `[bands(r) for r in records] == [(0, 45, None, None), (None, None, None, None)]`;
    - both triples are `(35, 80, 12)`, and `notes == []`.
  - New `test_band_minutes_are_none_without_a_scale_object`:
    - parametrized over `scale` in `[ABSENT, None, [600, 300, 60, 5], "600"]`, with ids `absent`, `null`, `list`, `string`;
    - the value is `figures()` for `ABSENT`, else `figures(stress_scale=scale)`;
    - expected: bands `(None, None, None, None)`, triple `(35, 80, 12)`, `notes == []`.
  - In `tests/test_cli.py`, `STRESS_DAY` gains `relaxed_min=600, mild_min=300, moderate_min=0, severe_min=None`.
- [ ] **C2: Run** `python -m pytest tests/test_domain_stress_daily.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **C3: Implement.**
  - Add the four fields after `min_stress_level`.
  - `_band_minutes(raw)` is `as_int(raw)` with a negative value turned into `None`.
  - `_summary` reads the keys `relax`, `mild`, `moderate`, `severe` of `value.get("stress_scale")` when it is a `dict`; otherwise all four are `None`.
  - Docstrings:
    - model: the minutes the day spent in each stress band, relaxed to severe, as the app shows them; `0` is a real value; with no wire names;
    - domain module: the band unit was checked against the app as minutes and the levels match the app (2026-10-15); this replaces "stays in `raw` only … unverified" and "not yet been compared";
    - `fetch_stress_daily`: the band rule;
    - `MiFit.stress_daily`: the bands are in `records`.
  - README `stress_daily` cell: "average, highest, lowest, and minutes in each stress band (relaxed, mild, moderate, severe)".
  - notes/domain-catalog.md §5, `stress_scale` bullet: "单位已对 app 核实为分钟（2026-10-15），四档进 B 层：`stress_daily` 的 `relaxed_min` / `mild_min` / `moderate_min` / `severe_min`".
- [ ] **C4: Run** `python -m pytest -q`. Expected: 1195 passed, 2 skipped (+5).
- [ ] **C5: Commit** `git commit -am "feat(stress_daily): band minutes from stress_scale (checked as minutes)"`

#### Part D: `UserProfile.activity_goal_min`

- [ ] **D1: Change and add tests.** In `tests/test_domain_profile.py`:
  - `test_maps_basics` and `test_missing_or_zero_basics_become_none` expect `activity_goal_min=30`. The default goal list holds `{"field": 4, "target": 30}`.
  - `test_unverified_and_personal_fields_stay_out_of_b`: the set gains `"activity_goal_min"`.
  - New `test_activity_goal_is_the_first_field_4_target`, parametrized over `regular_goal_list`. Each case expects `activity_goal_min == 45`:
    - `[{"field": 1, "target": 8000}, {"field": 4, "target": 45}]`
    - `[{"field": 4, "target": 45}, {"field": 2, "target": 300}]`
    - `["junk", None, {"field": 4, "target": 45}]`
    - `[{"field": 4, "target": 45}, {"field": 4, "target": 60}]`
    - `[{"field": 4.0, "target": 45.9}]`
  - New `test_activity_goal_is_none_when_absent_or_unusable`, parametrized over 12 goal lists:
    - `ABSENT` and `None`;
    - `{"field": 4, "target": 30}` (an object, not a list) and `[]`;
    - `[{"field": 1, "target": 8000}, {"field": 2, "target": 300}]`;
    - `[{"field": "4", "target": 30}]` and `[{"field": True, "target": 30}]`;
    - `[{"field": 4}]`, `[{"field": 4, "target": 0}]`, `[{"field": 4, "target": -30}]` and `[{"field": 4, "target": "30"}]`;
    - `[{"field": 4, "target": 0}, {"field": 4, "target": 30}]` (the first decides).

    Each case expects `records == [UserProfile(175.0, 70.0, 8000, 300, None)]`, `notes == []` and `raw == [sent]`.
  - New `test_step_and_calorie_goals_ignore_the_goal_list`:
    - with `regular_goal_list=[{"field": 1, "target": 9000}, {"field": 2, "target": 400}, {"field": 4, "target": 30}]`: `step_goal == 8000` and `calorie_goal_kcal == 300`;
    - with also `daily_step_goal=ABSENT`: `step_goal is None` (no fallback to field 1).
  - In `tests/test_cli.py`, `PROFILE` gains `activity_goal_min=30`.
- [ ] **D2: Run** `python -m pytest tests/test_domain_profile.py tests/test_cli.py -q`. Expected: FAIL.
- [ ] **D3: Implement.**
  - Add `activity_goal_min` as the last field.
  - `_activity_goal(goals)` returns `None` unless `goals` is a `list`. Otherwise it takes the first entry that is a `dict` with `as_float(entry.get("field")) == 4`, and returns `as_positive_int(entry.get("target"))`. Later entries are never consulted, and no case adds a note.
  - Docstrings:
    - model: the daily activity-time goal in minutes, as set in the app;
    - domain module: B also reads `regular_goal_list`'s field-4 entry; its fields 1 and 2 repeat the step and calorie goals and stay in `raw`; the unit of `daily_sleep_goal` is still unverified because the app page was not found;
    - `MiFit.profile` and the CLI module docstring: "daily step, calorie and activity-time goals".
  - README `profile` cell: the same wording.
  - notes/11:
    - §3, the bullet on field 4: it is the app's 目标活动时间 (minutes, checked 2026-10-15) and maps to `activity_goal_min`;
    - §5: `UserProfile(height_cm, weight_kg, step_goal, calorie_goal_kcal, activity_goal_min)` and its source.
- [ ] **D4: Run** `python -m pytest -q`. Expected: 1213 passed, 2 skipped (+18: 5 + 12 + 1). `git ls-files --eol` shows LF for every touched file.
- [ ] **D5: Commit** `git commit -am "feat(profile): add UserProfile.activity_goal_min (goal list field 4)"`

---

### Task 2: Public-repo cleanup (user decisions, checklist §3; no history rewrite)

**Files:**
- Modify (tests): `tests/test_domain_consumption.py`, `tests/test_client.py`, `tests/test_cli.py`.
- Modify (verification docs): `docs/superpowers/verification/2026-10-14-consumption-live.md`, `docs/superpowers/verification/2026-10-14-core-read-live.md`, `docs/superpowers/verification/2026-10-14-sleep-live.md`, `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` (one remark).
- Modify (roadmap): `docs/superpowers/roadmap.md`: §0 T1 row; §1 test line; the §3 M3 target bullet, M5 SpO2 bullet and M6 result; §4 T1 result.
- Modify (plans and spec):
  - `docs/superpowers/plans/2026-10-14-mifit-consumption-domain.md` (Task 1 Step 1)
  - `docs/superpowers/plans/2026-10-14-mifit-sleep-domain.md` ("Live-verified shapes")
  - `docs/superpowers/plans/2026-10-14-mifit-workouts.md` (Decision 5)
  - `docs/superpowers/specs/2026-10-14-mifit-fds-design.md` (§2, the `proto_type` 8 line)
- Modify (notes): `notes/03-crosscheck-mi-health-lab.md`, `notes/04-mi-health-lab-cloud-breakthrough.md`, `notes/05-calorie-field-investigation.md`, `notes/07-history-audit.md` (§5), `notes/10-workout-records.md` (§4, §5.3), `notes/11-spo2-weight-profile.md` (§1.2).
- Local only, git-ignored: `work/post-verification/figures.txt`, `calories.txt`, `phone.txt`.

**Interfaces:**
- Consumes the Task 1 test files as they are after Task 1. Task 2 only changes values.
- Produces wording that Task 3 builds on. Task 3 must not reintroduce any figure.

- [ ] **Step 1: Build the check lists (never committed).** Read each location from `main` (`git show main:<path>`), one per line, exactly as written there:
  - `work/post-verification/calories.txt`: the BMR value, the active value and their total, as used in the three test files and the consumption plan.
  - `work/post-verification/figures.txt`: every reading of three or more characters at the notes/07 §3 locations marked 有 or 按设计, plus the two locations below its table, plus `notes/11` §1.2. That means kcal values, bpm values with decimals, point counts, sleep minutes and clock times, and the SpO2 pair.
  - `work/post-verification/phone.txt`: the phone model and the OS name in the parenthesis of notes/03 §一's first bullet.
- [ ] **Step 2: Replace the test readings with synthetic values.** In the three test files:
  - the BMR value → `1500.5`;
  - the active value (`exercise` / `active_kcal`) → `250` (`250.0` where the test writes a float);
  - every total derived from them (resting + active) → `1750.5`. A total over the BMR alone becomes `1500.5`.

  No other number changes. Then run:
  - `python -m pytest -q`. Expected: 1213 passed, 2 skipped.
  - `git grep -n -w -F -f work/post-verification/calories.txt -- tests`. Expected: no output.
- [ ] **Step 3: Commit** `git commit -am "test: synthetic consumption values instead of real readings"`
- [ ] **Step 4: Rewrite the three `2026-10-14-*-live.md` docs.** These rules apply to every line:
  - Under the title, add: *Readings removed on 2026-10-15 (notes/07 §5): values are recorded as match / differ, and dates are relative to the run day D (2026-10-14).*
  - A reading (kcal, bpm, minutes, sleep clock times, scores, point counts) becomes the outcome of its comparison ("match", "differ", "present"), or is dropped. Never a new number.
  - A reading date becomes `D`, `D-1`, …. The run date and commit SHAs stay.
  - Status, Outcome, Decisions and Caveat keep their meaning.

  Per document:
  - **consumption-live:**
    - The first-run table becomes one sentence: D-4…D were read, and every record was labeled one day early (SDK date X held the app's day X+1).
    - Probe table: relative dates, no answered values. For example, `D-2 23:59:59Z | D-1 07:59 | D-1`.
    - Acceptance:
      - active matches the app on D-3…D-1, while D was still syncing;
      - resting is constant and equals the app's 静息卡路里 (the profile BMR);
      - intake matches;
      - total = resting + active.
    - Re-run: D-3…D-1 match, D was lower and still rising, and D-4 was not compared.
  - **core-read-live:**
    - Steps range: "about two weeks ending on D".
    - HR day: "D-1".
    - Windowed HR row: "range matches the app exactly; mean within rounding".
    - `--full` row: "completes; spans more than one day (not a same-day compare)".
    - Calories row: keeps "NO — mismatch". The ring, the step report's `calories` and the `calories` key differed from each other on the same days.
    - Notes row: drop the two HR values.
    - Add the row `Cross-check with mi-health-lab samples (check 4) | not done; open (end-of-run checklist §4)`.
  - **sleep-live:**
    - Columns become `D | D-1 | D-2`. A cell with a value becomes "match"; `none` and `None` cells stay as they are.
    - Score cells: "present".
    - Day D-3 row: "no record: no data that day; the SDK invents no zeros".
    - The stage-timeline sentence keeps "add up exactly" and drops the item count and the minutes.
    - Run command: `--from D-3 --to D`.
- [ ] **Step 5: Other health figures** (location → new wording; one Chinese or English line each, as the file is written):
  - roadmap §3 M3, "上次核对记下的目标" bullet → "上次核对时 app 显示的静息心率和睡眠心率要能对上（睡眠心率可能来自 M2 的睡眠报告）。"
  - roadmap §3 M5, SpO2 bullet → "已知 app 日均 ≠ 点的简单平均（notes/04 §4），聚合算法未知".
  - notes/04:
    - the headline's sleep parenthesis (segment and stage counts) → "睡眠(分段与分期与本地逐项一致)";
    - the §4 completeness bullet's point-count arithmetic → "（同日分两段读取的点数之和少于整日读取；可复现）";
    - the §4 "聚合算法未知" bullet → "血氧日均 ≠ 点的简单平均".
  - notes/05:
    - line 3: the four ring values and their dates → "（真机实测 4 天）";
    - items 1–3: drop the per-day lists, the BMR value ("恒定，来自个人资料") and "（本账号为 0）";
    - the line comparing the three sources → "三者与 app 环在任何时区错位下都对不上（有一天云端 `exercise` 几乎为 0，app 环却明显不为 0）。"
  - notes/11 §1.2: "（notes/04 §4：日均与点均不同）".
  - Sleep plan "Live-verified shapes":
    - "(a night and a nap on the same day)";
    - the state line → "`2` = deep, `3` = light, `5` = awake (each state's summed minutes equal the reported duration)".
  - Consumption plan Task 1 Step 1: the example response and `test_one_day_maps_fields` use the Step 2 synthetic values.
- [ ] **Step 6: Account facts.**
  - notes/03 §一, first bullet: the parenthesis keeps "用户真机" and "未 root" and drops the model and the OS.
  - notes/10 §4 intro: "目前核实过的四个 `sport_type` 和它们的中立名：".
  - notes/10 §5.3 coverage: "只有一个东八区账号；运动类型只核实了 §4 的四种".
  - Roadmap §3 M6 result: "其它运动（如爬楼机、力量训练，协议 8）".
  - FDS spec §2: "（例如爬楼机 300、力量训练 308，都是 `proto_type` 8 自由训练，无 GPS）".
  - M4 plan Decision 5: "The sport table covers only the four types verified so far".
  - Checklist §2.2, long-range row remark: "按月分块读取，条数与 app 一致，无提示".
- [ ] **Step 7: Record the decisions.**
  - notes/07 §5: append a paragraph "**2026-10-15 的决定**" that lists the choices:
    - 1–3: 后续提交删改（核对文档写一致 / 不一致、日期相对核对日，测试换合成值）, no history rewrite;
    - 4: 保留;
    - 5: 删手机型号, 保留地区和时区;
    - notes/10 §4 等处不再指明本账号的运动类型（映射表仍在代码里）.

    Note that only future versions change and that history keeps the old ones.
  - Roadmap §0 T1 row and §4 T1 result: append "2026-10-15 按 notes/07 §5 的决定在后续提交里删去健康数字和手机型号，未改写历史".
  - Roadmap §1 test line: replace the parenthesis about the 3 test files with "（假 Transport；测试数据全为合成值）".
- [ ] **Step 8: Verify.**
  - The figure grep, restricted to the docs this task edits: `git grep -n -w -F -f work/post-verification/figures.txt -- docs/superpowers/verification docs/superpowers/roadmap.md docs/superpowers/plans/2026-10-14-mifit-consumption-domain.md docs/superpowers/plans/2026-10-14-mifit-sleep-domain.md docs/superpowers/plans/2026-10-14-mifit-workouts.md docs/superpowers/specs/2026-10-14-mifit-fds-design.md notes/03-crosscheck-mi-health-lab.md notes/04-mi-health-lab-cloud-breakthrough.md notes/05-calorie-field-investigation.md notes/07-history-audit.md notes/10-workout-records.md notes/11-spo2-weight-profile.md`.
    - Expected: no reading. A short list has many collisions, so judge each remaining hit by eye against `main`. Only non-readings may remain, such as a synthetic fixture value in the FDS spec.
  - These commands must print nothing:
    - `git grep -n -i -F -f work/post-verification/phone.txt`
    - `git grep -n -E "2026-10-(0[1-9]|1[0-3])" -- docs/superpowers/verification/2026-10-14-*.md`
    - `git grep -n -E "(^|[^0-9-])10-(0[1-9]|1[0-4])([^0-9]|$)" -- docs/superpowers/verification/2026-10-14-*.md notes/05-calorie-field-investigation.md docs/superpowers/plans/2026-10-14-mifit-sleep-domain.md`
    - `git grep -n "本账号的爬楼机\|账号上出现过的四个\|the account's four types" -- . ':!docs/superpowers/plans/2026-10-15-mifit-post-verification.md'`
  - Also check that `python -m pytest -q` still gives 1213 passed, 2 skipped, that `git status` shows nothing under `work/`, and that the files are LF.
- [ ] **Step 9: Commit** `git commit -am "docs: remove real health figures, the phone model and the account's sport types (notes/07 §5 decisions)"`

---

### Task 3: Status documents after verification

**Files:**
- Modify: `README.md` (domain table, the sentence under it, Known limitations), `docs/superpowers/roadmap.md` (§0, §1, §2, §3 result lines, §4 T2, §6).
- Modify: `notes/09-session-lifetime-renewal.md` (§4, §5), `notes/10-workout-records.md` (§5.3), `notes/11-spo2-weight-profile.md` (§4), `notes/12-fds-workout-detail.md` (§10), `notes/domain-catalog.md` (`已核对` cells, legend, §2 候选 note, §5 候选 bullet), `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` (status line, §4 first bullet).

**Interfaces:**
- Consumes the Task 1 names (`total_kcal`, `relaxed_min` / `mild_min` / `moderate_min` / `severe_min`, `activity_goal_min`, the `WorkoutSample` without altitude) and the Task 2 wording.
- Every "see the verification" pointer goes to `docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` with its section number.

- [ ] **Step 1: README.**
  - "Checked against the app" column:
    - `heart_rate_daily`, `workouts`, `spo2_daily`, `spo2`, `stress_daily` and `profile`: `yes`;
    - `weight`: "yes; body composition could not be checked and is in `raw` only";
    - `workout_detail`: "yes (one outdoor run, one ride); altitude is in `raw` only: the run's was a few metres off the app's".
  - Under the table: drop the "Pending" sentence.
  - Known limitations:
    - **"Workout detail reads outdoor runs and rides only."** It replaces "Workout detail is new." The formats are unchanged. Walks, hikes, swims, indoor runs and other sports, and other file versions, get a note and no samples: each needs a real file and a hand-written layout table first. Altitude is in `raw` only. The 10-workout cap, the track-privacy sentence and the links sentence stay.
    - New **"Workout files outside the China region are untested."** Files download only from hosts under `.fds.api.xiaomi.com`, the host seen on a China-region account. A link to another host is refused with a note, and that workout keeps its summary without samples or track (spec §16 Q8).
    - **"Sessions expire."** Renewal was checked live on 2026-10-15. The lifetime without renewal is still being observed and is at least 22 hours so far. The rest stays.
- [ ] **Step 2: Roadmap.**
  - §0 status cells:
    - M3: "✅ 完成；2026-10-15 已真机核对（PASS）".
    - M4: "… PASS；`total_cal` 核实为总卡路里，加入 B 层 `total_kcal`".
    - M5: "… PASS；目标活动时间进 B 层；身体成分无数据源，只在 A 层".
    - M6: "… 部分：海拔移回 A 层，步频 0 改为保留".
    - M7: "✅ 一轮完成；`stress_daily` 2026-10-15 已真机核对（PASS，各档时长进 B 层）；候选 key 未核对（app 里没找到对应页面）；目录持续维护".
    - T2: "✅ 完成；2026-10-15 已真机核对（续期可用；寿命下界 ≥ 22 h，观察中）".
  - §1 domain list:
    - drop every "待真机核对";
    - `workouts`: add 总卡路里 `total_kcal`;
    - `workout_detail`: list the sample fields without 海拔 and add "海拔只在 `raw`";
    - `stress_daily`: the four band fields replace "`stress_scale` 只留在 `raw`";
    - `profile`: add 目标活动时间 `activity_goal_min`;
    - test count: "1213 个通过、2 个跳过".
  - §2 T2 row: "→ **已实现并真机核对（T2，2026-10-15）**". The row says:
    - the renewal path works live (forced 401 → one renewal, `created_at` unchanged, no repeat);
    - the lifetime lower bound is ≥ 22 h;
    - natural expiry is still observed via the session log (notes/09 §5).

    The impact cell: "真实寿命仍待日志观测".
  - §3: each M3–M7 "结果（…）✅（待真机核对）" becomes "✅". Each gets an appended "**真机核对（2026-10-15）**：…" line built from the "Verified on 2026-10-15" list above, ending with "清单 2.x 节". For M6, the earlier "海拔暂进 B…待真机定" clause is followed by "核对后移回 A 层".
  - §4 T2 result: the same pattern (checklist §1, notes/09 §5).
  - §6:
    - M6 node: append "Q9 已答（2026-10-15）：维持现状，户外步行 / 徒步、游泳、室内跑等以后有真实文件时再补布局表".
    - M7 node: "✅ 已做（2026-10-15）：压力日汇总与各档时长一致；站立小时数、中高强度分钟数、活动卡路里在 app 里没找到对应页面，候选 key 仍未核对".
- [ ] **Step 3: notes/09.**
  - §5: add a 2026-10-15 bullet:
    - the pre-T2 session was readable about 22 h after login → serviceToken lifetime **≥ 22 h**, with no natural expiry seen yet;
    - QR login with deviceId works;
    - forced 401: `unauthorized` → `renewed`, `renewed_at` set, `created_at` unchanged;
    - the next export logged no event.
  - §5: replace "（Task 3–5 在此追加…）" with "（自然过期和 passToken 寿命的观测继续在此追加。）"
  - §4, deviceId item: login and renewal with the same `device_id` both worked live. Whether a new deviceId invalidates the passToken is still unknown.
  - §4, serviceLogin item: the live renewal confirms the JSON answer.
- [ ] **Step 4: notes/10 §5.3, notes/11 §4, notes/12 §10.**
  - notes/10 §5.3:
    - "`calories` 与 `total_cal`" → 已核实: the app shows both (`active_kcal` / `total_kcal`).
    - "`duration` 与 `end − start`" → 已核实: the app's duration excludes pauses and equals `duration_s`.
  - notes/11 §4:
    - Profile item: field 4 is answered (→ `activity_goal_min`); the `daily_sleep_goal` unit is still open because the app page was not found.
    - Add "核对结果（2026-10-15，清单 2.3–2.6）": spo2_daily, spo2, weight and profile match the app. Body composition: no weight data source (manual import only), all 0, raw only.
  - notes/12 §10: "已回答" and "仍开放" sub-lists, with the same Q numbers.
    - Answered:
      - Q4: ride about 0.1 m off, run extremes 2–3 m off → `altitude_m` left B;
      - Q6: the app shows 0 → `0.0` kept;
      - Q8 → README limitation, allow-list unchanged;
      - Q9: status quo (walk, hike, swim, indoor later, real file first);
      - Q10: agreed; the local SVG matched the app map;
      - plus a new line on cadence (the app's average includes zero seconds → `0` kept).
    - Open:
      - Q1: start/end and moving-track length match the app, but there is no direct evidence that the app's distance counts only the moving part, and pause positions were not found in the app;
      - Q2: average, max and min (and the curve) match, but how the app draws zero-bpm seconds is unanswered;
      - Q3 and Q7: unanswered;
      - Q5: per-km pace matches, but instantaneous versus smoothed is unanswered.
- [ ] **Step 5: Catalog.**
  - The rows daily `heart_rate`, daily `spo2`, daily `stress`, raw `spo2` and raw `weight` go from `待真机核对` to `app 已核对`.
  - The legend's `待真机核对` line reads "已进 B 层，等 app 对照".
  - §2 候选 bullet: append "2026-10-15 收尾核对时 app 里没找到对应页面，仍未核对".
  - §5 "候选 key 的 app 对照" bullet → "已做（2026-10-15，收尾清单 2.8）". `stress_daily` levels and bands match (`app 已核对`). `valid_stand` / `intensity` / `calories` stay 候选 because their pages were not found.
- [ ] **Step 6: Checklist.**
  - Status line: "本清单即这次核对的正式记录，不另写各域核对文档（2026-10-15 决定）；只记 PASS / 差异 / 未核对和原因，不含读数、读数日期或 id。"
  - §4 first bullet: append "→ 2026-10-15 决定：不另写，本清单即核对记录".
- [ ] **Step 7: Verify.**
  - `python -m pytest -q` gives 1213 passed, 2 skipped (the catalog guards are included).
  - `git grep -n -i "pending" -- README.md` prints nothing.
  - `git grep -n "待真机核对" -- README.md docs/superpowers/roadmap.md notes` prints only the catalog legend line.
  - `git grep -n -w -F -f work/post-verification/figures.txt -- README.md docs/superpowers/roadmap.md notes docs/superpowers/verification/2026-10-15-end-of-run-checklist.md` prints the same lines as on the Task 2 commit (run it there first). Those lines are all non-readings, so this step adds no hit.
  - `git ls-files --eol` shows LF for the touched files.
- [ ] **Step 8: Commit** `git commit -am "docs: post-verification status (README, roadmap, notes, catalog)"`. Do not push.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Field order and names.**
   - `total_kcal` sits right after `active_kcal`, so exported JSON keeps the two energy figures together.
   - The bands follow `min_stress_level` in the order relaxed → severe.
   - `activity_goal_min` comes last.
   - The new fields have no defaults, like every other field. Code that builds these dataclasses by hand must pass them; only the SDK builds them.
   - The names `relaxed_min`, `mild_min`, `moderate_min` and `severe_min` are neutral: the wire keys `relax` / `mild` / `moderate` / `severe` stay in the domain.
2. **Band minutes use `as_int`, and a negative value becomes `None`.** `0` is a real value, as the spec asks. A negative count cannot be a duration, and the service never sent one. A `stress_scale` that is not an object gives four `None`s and no note, like the other figures.
3. **Field-4 matching:**
   - the first object entry whose `field` is the number 4 (`as_float(field) == 4`; a bool or a string never matches);
   - its `target` alone decides; a later field-4 entry is never consulted;
   - a target that is not positive gives `None` ("not set", the same rule as the step and calorie goals);
   - there is no fallback from `step_goal` / `calorie_goal_kcal` to fields 1 and 2.
4. **The altitude code is deleted from `to_samples`**, not kept behind a flag. The A layer still decodes `initialAltitude` and the per-second bits, and a domain test pins that. The three altitude mapping tests go with the code.
5. **Docstring-only items:** `speed_mps = 0.0` (Q6). `duration_s` needs no change, because the app's duration is exactly it. `pace_s_per_km` keeps `0 → None`: the check did not cover it.
6. **Scope of the figure cleanup:**
   - The list is notes/07 §3, plus `notes/11` §1.2, which grep shows quoting notes/04's SpO2 pair. In the same files, counts of the account's own records go too (a day's HR point counts, a night's segment and stage counts).
   - Per-window row counts in notes/08, notes/11 and notes/12 and in the roadmap's M3/M6 results stay. They are the experimental evidence of the bucket rule and of the decoder gate, and they carry no measurement.
   - Bucketed counts (`1–10`, `>10`) stay, following the precedent of `e9f0ee3`.
7. **Verification docs keep their language.** English docs say "match" / "differ". Reading dates become `D`-relative, and the run date stays. The page structure and the outcomes are unchanged.
8. **Synthetic consumption values:** BMR `1500.5`, active `250`, total `1750.5`. The BMR keeps a fraction so float handling is still exercised.
9. **Sport-type wording:** decision 6 is applied to every current sentence that names the account's non-GPS sport types: notes/10 §4/§5.3, the roadmap's M6 result, FDS spec §2 and M4 plan Decision 5. Without that, the same fact would stay public elsewhere. Run and ride mentions stay, because the shipped layouts already imply them.
10. **The checklist is the verification record.** No per-domain `*-live.md` docs are written. Its filled-in remarks stay as approved, except the count of monthly chunks: that count reveals how long the account has existed, so it goes. Qualitative facts the user chose to record (no weight data source, a day not worn) stay.
11. **core-read-live check 4 is not done here.** It needs a live export and the private mi-health-lab samples outside the repo. Task 2 records it as "not done; open", and it stays open in checklist §4.
12. **notes/09 §4 is updated with §5**, because the live run answers two of its unknowns (the serviceLogin JSON answer, and the deviceId on login and renewal).
13. **Every catalog row that was `待真机核对` and was checked becomes `app 已核对`**, not only `stress`. Leaving them would contradict the README.
14. **Docs follow code per commit.** Task 1 updates the text that describes each B field (README "What you get", notes tables, docstrings). Task 3 updates the text that describes verification state. The roadmap changes only in Tasks 2 and 3.
15. **Execution:** Native is recommended. There are three sequential tasks, mostly mechanical, with one interface hand-off (Task 1's field names), followed by one fresh reviewer over the whole branch.

## Self-Review

1. **Spec coverage:**
   - Checklist results:
     - 2.2 `total_kcal` → Part A;
     - 2.7 items 6 and 8 and Q6 → Part B;
     - 2.8 `stress_scale` → Part C;
     - 2.6 goal list → Part D.
   - §3 decisions:
     - 1–3 → Task 2 Steps 2–5;
     - 4 → kept (Decision 6 context);
     - 5 → Step 6;
     - 6 → Step 6 / Decision 9;
     - 7 → README and notes/11 (Task 3);
     - 8 → README limitation and notes/12 Q9.
   - §4 收尾:
     - per-domain docs → Decision 10;
     - B changes → Task 1;
     - README, roadmap, notes and catalog → Task 3;
     - core-read check 4 → Decision 11;
     - health figures and notes/10 → Task 2.
   - The parent request: all three tasks with every listed file. ✓
2. **Step scan:** every code step names the file, the field and the exact rule. Every test step names its test and its values. Every doc step names the location and the new wording, or the fact it must state. Figures are referenced by location only. ✓
3. **Type consistency:** `total_kcal`, `relaxed_min` / `mild_min` / `moderate_min` / `severe_min`, `activity_goal_min`, `_band_minutes`, `_activity_goal` and the seven-field `WorkoutSample` are spelled the same in Tasks 1–3 and in the test constants. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1. ✓
5. **Proportion:** three tasks. No function body is written out; the only literals are test values and the exact replacement wording that the decisions fix. ✓
