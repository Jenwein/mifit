# Live verification: core read path (steps, heart rate)

**Status:** PENDING — awaiting user QR login (requires the user to scan with their phone; cannot be run autonomously)

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

5. Count points in `hr.json` (windowed) and `hr_full.json` (`--full`) for the same day. Record both counts and whether they differ. This is the known incompleteness in `notes/04-mi-health-lab-cloud-breakthrough.md` §4: windowed reads can drop points.
6. Check `notes`. The windowed read always carries a "MAY BE INCOMPLETE" note. The `--full` read stops after 10 pages, and that cap ends quietly with no note. If the full count looks capped, record it.
7. A count difference is a finding, not a failure. It does not change the steps outcome.

**Outcome**
- **Pass:** checks 1 to 4 hold for steps, and both heart-rate exports complete without errors.
- **Partial:** steps match on some days but not all, a date offset is found, or the cross-check could not be done. Record which days and why.
- **Fail:** login, decryption or parsing fails, or steps differ from the app beyond rounding. Record the error message or the differing dates, not the values.

## 3. Results

**Status:** PENDING — awaiting user QR login (requires the user to scan with their phone; cannot be run autonomously)

| Item | Result |
|---|---|
| Run date and who ran it | |
| Steps date range used (`--from` / `--to`) | |
| Steps: days compared / all match within rounding (y/n) | |
| Steps: week total matches app (y/n) | |
| Steps: date labels align with app days (y/n; offset if any) | |
| mi-health-lab cross-check (y/n; dates used) | |
| Heart-rate day used | |
| Heart rate: windowed point count | |
| Heart rate: `--full` point count | |
| Heart rate: counts differ (y/n) | |
| Heart rate: `--full` hit the 10-page cap (y/n) | |
| Outcome (pass / partial / fail) | |
| Notes (errors and messages only; no health values or tokens) | |

## 4. Gate note

This run is the M1 gate: the steps domain end to end, checked against the app (plan Task 11; design §10). It needs the user present to scan the QR code. Until a run is done and the table above is filled in, the status stays PENDING and nothing in this file counts as verified.
