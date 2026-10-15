# mifit Consumption (Calories) Domain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a read-only "consumption" (calories) domain exposing the cloud's resting/active/intake calories, and rename the misleading `StepRecord.calories` to `step_calories`.

**Architecture:** New domain `fetch_consumption` calling the per-day endpoint `GET app/v1/diet/user_consumption` (single `HeatConsumptionData{basal,exercise,intake}` per day, not paginated) via the existing `Transport.get_json`; a neutral `ConsumptionRecord` model; wired into `MiFit` + CLI through the existing REGISTRY. Separately, rename the step model's `calories` field to `step_calories` so it is not confused with the app's calorie ring (design anti-goal: don't let a B field mislead).

**Tech Stack:** Python 3.12+, existing mifit modules, pytest. No new deps.

**Spec:** findings in `notes/05-calorie-field-investigation.md` (and design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md`).

## Global Constraints

- Read-only. Endpoint `diet/user_consumption` (GET, query param `data`), request JSON `{"time": <int Unix seconds within the target day>}` (NOT ms, NOT a date string — server rejects those with "invalid time"). Response `HeatConsumptionData{basal:float, exercise:float, intake:float}`, wrapped in the `result` envelope like other calls; `basal`=resting/BMR (~constant from profile), `exercise`=active calories (cloud-synced), `intake`=food.
- Faithful A + neutral B: `ConsumptionRecord` uses neutral names; `raw` keeps the decoded `{basal,exercise,intake}` dict untouched.
- The endpoint returns ONE day per request → `fetch_consumption` iterates each calendar day in the range and calls `get_json` once per day. Use `time` = that day's 00:00 UTC + 43200 (noon UTC) as the representative; label `ConsumptionRecord.date` by the UTC calendar date of the day start and `epoch_s` = day-start epoch. (Day-bucketing timezone is confirmed in the live-verify task; adjust there if the app's day differs.)
- No fabricated values: if a day's response is missing/zero-only or errors, record a note and skip that day's record (keep whatever `raw` came back). `intake` may legitimately be 0.
- Credentials/health values never printed or committed; offline tests use a fake transport.
- `transport.get_json` already unwraps `result` for the top-level `code` check and returns the full decrypted dict; read `basal/exercise/intake` from `payload.get("result", payload)`.

## Review Focus

- **Per-day loop correctness:** a multi-day range must produce one record per day with the right `time` sent and the right `date` label; an inverted or single-day range must still work. → tested in Task 1.
- **Missing/zero day:** a day the server has no data for must not fabricate numbers; note it. → tested in Task 1.
- **`result` unwrap:** values must be read from the `result` wrapper, not the top level. → tested in Task 1.
- **Rename completeness:** every reference to `StepRecord.calories` (model, steps domain mapping, fixture, steps tests, client test, any export) must move to `step_calories`; nothing may still read `.calories`. → tested in Task 2.
- **CLI/registry wiring:** `consumption` appears in `mifit export` choices and dispatches to `MiFit.consumption`. → tested in Task 2.

---

### Task 1: ConsumptionRecord model + consumption domain

**Files:**
- Create: `src/mifit/models/consumption.py`, `src/mifit/domains/consumption.py`, `tests/test_domain_consumption.py`
- Test: as above

**Interfaces:**
- Consumes: `Transport.get_json(endpoint_path, data_obj, *, sign_path=None) -> dict`; `DomainResult`, `register` from `domains.base`.
- Produces:
  - `models/consumption.py`: `@dataclass ConsumptionRecord{ date: str, epoch_s: int, resting_kcal: float | None, active_kcal: float | None, intake_kcal: float | None, total_kcal: float | None }` (neutral names; `total_kcal = resting_kcal + active_kcal` when both present, else None).
  - `domains/consumption.py`: `@register("consumption") def fetch_consumption(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult` — iterates days, `get_json("diet/user_consumption", {"time": day_start_utc + 43200})`, maps `result.{basal,exercise,intake}` → `ConsumptionRecord` (basal→resting_kcal, exercise→active_kcal, intake→intake_kcal), `raw` gets the decoded `{basal,exercise,intake}` dict per day.

- [ ] **Step 1: Write failing tests** `tests/test_domain_consumption.py` (fake Transport whose `get_json` returns a scripted per-`time` response, e.g. `{"code":0,"result":{"basal":1500.5,"exercise":250,"intake":0}}`; NO network):
  - `test_one_day_maps_fields`: single-day range → one `ConsumptionRecord(resting_kcal=1500.5, active_kcal=250, intake_kcal=0, total_kcal=1750.5)`, `raw==[{"basal":1500.5,"exercise":250,"intake":0}]`.
  - `test_range_yields_one_record_per_day_with_correct_time`: a 3-day range → 3 records; assert the `time` values sent are each day's `00:00 UTC + 43200` and dates are consecutive UTC dates.
  - `test_missing_day_is_noted_not_fabricated`: a day whose response has no usable values (or get_json raises) → no record for it, a note added; other days still returned.
  - `test_values_read_from_result_wrapper`: a response nested under `result` is read correctly (not the top level).
- [ ] **Step 2: Run tests, verify fail** — `python -m pytest tests/test_domain_consumption.py -v`.
- [ ] **Step 3: Implement** `models/consumption.py` and `domains/consumption.py` per the interfaces. Day iteration: from `start_epoch_s` floored to its UTC day, step 86400 up to `end_epoch_s`. `total_kcal` only when both resting and active present.
- [ ] **Step 4: Run tests, verify pass**; then full `python -m pytest tests/ -v`.
- [ ] **Step 5: Commit** — `git commit -m "feat(domains): consumption (calories) domain + ConsumptionRecord"`

---

### Task 2: Wire into client + CLI; rename StepRecord.calories → step_calories

**Files:**
- Modify: `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/models/steps.py`, `src/mifit/domains/steps.py`, `tests/fixtures/steps_daily_sample.json` (only if it keys on the field name), `tests/test_domain_steps.py`, `tests/test_client.py`
- Test: `tests/test_client.py` (add consumption + rename cases), plus the steps tests updated

**Interfaces:**
- Consumes: Task 1 `fetch_consumption`, `ConsumptionRecord`.
- Produces: `MiFit.consumption(self, start: DateArg, end: DateArg) -> DomainResult` → `fetch_consumption(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True))`. Import the consumption domain module in client.py's domain-import block so REGISTRY gets `consumption`. Export `ConsumptionRecord` from `mifit/__init__.py`. Rename `StepRecord.calories` → `step_calories` (update its docstring to say it is the step report's own walking-calorie figure, NOT the app's calorie ring).

- [ ] **Step 1: Write failing tests**:
  - In `tests/test_client.py`: `test_consumption_delegates_to_domain` (patched/fake transport → returns the consumption DomainResult); confirm `consumption` is in `REGISTRY` after importing `mifit.client`.
  - In `tests/test_domain_steps.py`: update the existing assertions to `step_calories` (the record field is renamed); add/keep a case proving the field is named `step_calories` and carries the walking-calorie value.
- [ ] **Step 2: Run tests, verify fail** (rename not yet applied; new consumption method missing).
- [ ] **Step 3: Implement** the rename across `models/steps.py` (field + docstring), `domains/steps.py` (the mapping that sets it), and any reader; add `MiFit.consumption`, the client domain-import, the `__init__` export. Grep to ensure no remaining `.calories` reference to StepRecord (the Consumption model uses its own fields; step-report walking calories everywhere becomes `step_calories`).
- [ ] **Step 4: Run tests, verify pass**; full `python -m pytest tests/ -v`; `python -m pip install -e . && mifit export --help` shows `consumption` in choices.
- [ ] **Step 5: Commit** — `git commit -m "feat(client,cli): expose consumption; rename StepRecord.calories to step_calories"`

---

### Task 3: Live verification (manual, gated on session — NOT CI)

**Files:** Create `docs/superpowers/verification/2026-10-14-consumption-live.md`

- [ ] **Step 1:** With the existing `~/.mifit/session.json`, run `mifit export consumption --from <recent> --to <today> --json consumption.json` (outside the repo).
- [ ] **Step 2:** Record `resting_kcal` (should be ~constant BMR), `active_kcal`, `intake_kcal`, `total_kcal` per day. Confirm the per-day `date` aligns with the app's day (adjust the `time` offset in Task 1 if the app's day differs). Compare `total`/`active` with the app, noting that recent days may lag (cloud vs app on-device blend; see notes/05).
- [ ] **Step 3:** Record outcome (pass/partial) + the calorie-ring caveat. No tokens/values committed beyond pass/fail + non-sensitive notes.
- [ ] **Step 4: Commit** the verification doc.

---

## Self-Review
1. Spec coverage: consumption domain exposing resting/active/intake/total (Task 1) ✓; client+CLI+registry (Task 2) ✓; StepRecord.calories rename (Task 2) ✓; live verify + caveat (Task 3) ✓.
2. Step scan: each code step gives a signature/file; fixtures carry exact synthetic values; no decide-nothing lines.
3. Type consistency: `ConsumptionRecord`/`fetch_consumption` names match across Tasks 1-2; `MiFit.consumption` returns `DomainResult`; registry name `consumption` used by CLI.
4. Review Focus: per-day loop, missing day, result unwrap → Task 1; rename completeness, CLI wiring → Task 2.
5. Proportion: small plan for a small additive change; mostly signatures + synthetic fixtures.
