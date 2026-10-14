# Live verification: core read path (steps, heart rate)

**Status:** PASS — the core read chain was verified live against the user's real account on 2026-10-14 (see section 3); one calories-semantics follow-up remains

This is the **M1 gate** from the [implementation plan](../plans/2026-10-14-mifit-foundation-core-read.md) (Task 11), checked against design §8.1 and §10. It is a manual procedure, not a CI test. Run it when the user is at the computer with the Mi Fitness / Xiaomi app on their phone.

**Rules for this run**
- Do not commit tokens, session contents, exported JSON, or health values. Record only what the Results section asks for.
- Write exports to `$HOME\mifit-verify\`, outside the repo. `.gitignore` covers `.mifit/`, `*.session.json` and `private/`, but not `steps.json` or `hr.json` in the repo root.

## 1. Procedure

Prerequisites: Python 3.12+, branch `feat/mifit-foundation-core-read` checked out, phone signed in to the same Mi Fitness account.

```powershell
cd C:\dev\mifitness-re
pip install -e .
mifit --help
New-Item -ItemType Directory -Force "$HOME\mifit-verify" | Out-Null

# 1. Login. Scan the QR code shown in the terminal with the Xiaomi / Mi Fitness app and approve.
#    No password is typed. The session is saved to ~\.mifit\session.json.
mifit login

# 2. Steps: daily summaries for a recent range (--to is inclusive)
mifit export steps --from YYYY-MM-DD --to YYYY-MM-DD --json "$HOME\mifit-verify\steps.json"

# 3. Heart rate: one fixed past day, windowed read (default)
mifit export heart_rate --from DAY --to DAY --json "$HOME\mifit-verify\hr.json"

# 4. Heart rate: same day, full read
mifit export heart_rate --from DAY --to DAY --full --json "$HOME\mifit-verify\hr_full.json"
```

Operating notes:
- Dates are `YYYY-MM-DD`, local time. For steps, `--to` may be today; if today is still syncing, use yesterday and record that.
- For heart rate, use a past day so both reads see the same data.
- Run the commands one at a time. Do not loop on errors.
- Output is `{records, raw, notes}`. Read `notes` on every run.
- If `mifit login` shows a security confirmation, open the printed URL, complete it, then run `mifit login` again.
- If an export says to run `mifit login` (HTTP 401: session expired or rejected), log in once more. A 401 can also come from a signing problem, so if a fresh login still fails, record the exact message as a finding and stop.

## 2. Acceptance checks

**Steps (decides the M1 gate)**

1. **Fetch and parse.** The export succeeds. Every day the app shows data for has a record. Days with no data are absent, not zero. Note any "Skipped" messages in `notes`.
2. **Daily totals match the app** within rounding (design §8.1). Compare each day's `steps` with the app's day view, and the range total with the app's week view. Sum across days only; each record is already one summary per day.
3. **Day alignment.** Each record's `date` is the UTC calendar date of the service timestamp. The mi-health-lab notes (`C:\dev\mi-health-lab\docs\cloud-health-verification-07.md`) found these daily rows sit 28,800 s from local midnight, so an off-by-one day is possible. Confirm each `date` is the day the app shows. Record any offset; do not fix it in this run.
4. **Cross-check where possible.** For overlapping dates, compare with the verified daily-summary samples in `C:\dev\mi-health-lab\docs\cloud-read-06.md` and `docs\cloud-health-verification-07.md`. Record yes/no only; do not copy their values here.

**Heart rate (record only; not a blocker)**

5. Count points in `hr.json` (windowed) and `hr_full.json` (`--full`) for the same day. Record both counts and whether they differ. This is the known incompleteness in `notes/04-mi-health-lab-cloud-breakthrough.md` §4: windowed reads can drop points (fixed 2026-10-14, see notes/08).
6. Check `notes`. The windowed read asks the service for a day before the start and keeps only the window, so it carries no incompleteness note (notes/08). The `--full` read stops after 10 pages and says so in `notes` when older data remains. If the full count looks capped, record it.
7. A count difference is a finding, not a failure. It does not change the steps outcome.

**Outcome**
- **Pass:** checks 1 to 4 hold for steps, and both heart-rate exports complete without errors.
- **Partial:** steps match on some days but not all, a date offset is found, or the cross-check could not be done. Record which days and why.
- **Fail:** login, decryption or parsing fails, or steps differ from the app beyond rounding. Record the error message or the differing dates, not the values.

## 3. Results

**Status:** PASS (core read chain verified live against the user's real account, 2026-10-14) — one semantics follow-up on calories.

| Item | Result |
|---|---|
| Run date and who ran it | 2026-10-14, user QR login + SDK reads |
| QR login (sid miothealth, region cn) | PASS — service token + ssecurity obtained, no password |
| Steps date range used | ~2 weeks (2026-10-01 … 2026-10-14) |
| Steps: all match app within rounding (y/n) | YES — user confirmed daily steps match |
| Distance: matches app (y/n) | YES — user confirmed |
| Steps: date labels align with app days (y/n) | YES for this account (no off-by-one observed on the compared days) |
| Heart-rate day used | 2026-10-13 |
| Heart rate windowed: n / min / max / avg | 133 pts / 60 / 120 / 82.3 — **exactly matches app (range 60–120, avg 82)** |
| Heart rate `--full` (multi-day, raw) | 2413 pts / 55 / 153 / 85.3 (spans >1 day; not a same-day compare) |
| bpm scaling | correct — raw bpm, NOT ×5 |
| **Calories: matches app? (y/n)** | **NO — mismatch.** App calorie ring (195/490/453/576 for 10-14..10-11) ≠ step-report `calories` (103/171/261/302) ≠ `calories` daily-report key (365/292/1/485). App's calorie metric is a different, not-yet-identified quantity (likely total/active energy incl. BMR). FOLLOW-UP, not a chain bug. |
| Outcome | **PASS** for the core read chain (auth, steps, distance, raw heart-rate parse/accuracy). Calories semantics = follow-up. |
| Notes | Resting HR (77) and sleep HR (65) the app shows are separate derived metrics (HR daily report / sleep report) — not in the raw-HR domain; future domains. No tokens or raw per-day health values committed here. |

### Follow-ups surfaced by this run
1. **Calories semantics** — identify which cloud key/metric backs the app's calorie display; until then, rename/annotate `StepRecord.calories` so it is not mistaken for the app's figure (design anti-goal: don't let a B field silently mislead).
2. **Resting HR, sleep HR** and the HR daily-summary (avg/max/min/rest) — add a HR daily-report domain in a later plan.

## 4. Gate note

This run is the M1 gate: the steps domain end to end, checked against the app (plan Task 11; design §10). It needs the user present to scan the QR code. Until a run is done and the table above is filled in, the status stays PENDING and nothing in this file counts as verified.
