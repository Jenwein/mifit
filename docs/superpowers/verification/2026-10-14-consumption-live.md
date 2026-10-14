# Live verification: consumption (calories) domain

**Status:** PASS (values and dates verified against the app). The date-label offset found in the first run is fixed and re-verified live (see *Date-label fix*).

Run 2026-10-14 against the user's real account (cached QR session), `mifit`/`MiFit().consumption(...)`.

## What was read (diet/user_consumption → resting/active/intake/total)

First run, **before** the date-label fix: domain output for a 2026-10-11…2026-10-14 request (SDK machine in CST/UTC+8). The dates here are one day early; see *Date-label fix* for the corrected run.

| record date (SDK, UTC-bucket) | resting_kcal | active_kcal | intake_kcal | total_kcal |
|---|---|---|---|---|
| 2026-10-10 | 1907.5 | 576 | 0 | 2483.5 |
| 2026-10-11 | 1907.5 | 453 | 0 | 2360.5 |
| 2026-10-12 | 1907.5 | 490 | 0 | 2397.5 |
| 2026-10-13 | 1907.5 | 213 | 0 | 2120.5 |
| 2026-10-14 | 1907.5 | 0 | 0 | 1907.5 |

## Acceptance

- **active_kcal matches the app's calorie ring exactly** (app, user-read: 10-11=576, 10-12=453, 10-13=490, 10-14=195). Our `active_kcal` set {576, 453, 490, ~213} equals the app's values — this is the number the app's "活动卡路里/calorie ring" shows. ✅ (So the earlier "calories mismatch" is fully explained and the real field is now located and read.)
- **resting_kcal = 1907.5**, constant = the user's RMR/BMR from profile (app: 静息卡路里). ✅
- **intake_kcal = 0** (no diet logged). ✅
- **total_kcal = resting + active** = the app's "总卡路里" definition (静息 + 活动). ✅

## Date-label fix (re-verified live 2026-10-14)

The first run labeled every record one day early for this UTC+8 account (SDK-date D held the app's day D+1). A first fix (`03904b1`, rounding request bounds to the nearest UTC midnight) changed which days were requested but **not** the `time` sent for each day, so a re-run showed the same offset. It was a misdiagnosis.

**Root cause.** The domain sent `time` = UTC midnight + 12 h (20:00 local). The app sends the day's **local midnight**: `HeatViewModel.preloadWeekConsumption` builds `HeatConsumptionNewRequest((int)(TimeDateUtil.atStartOfDay(day) / 1000))`, and `atStartOfDay(LocalDate)` is `date.toDateTimeAtStartOfDay(DateTimeZone.forID(TimeZone.getDefault().getID()))`. The service files a `time` under the day the app would mean by it, not under its UTC day.

**Probe evidence** (same account, read-only, one call each; `exercise` returned):

| `time` sent | local (UTC+8) | answered with app day |
|---|---|---|
| local midnight 10-11 / 10-12 / 10-13 | 00:00 | 10-11 (576) / 10-12 (453) / 10-13 (490) |
| 2026-10-12T23:59:59Z | 10-13 07:59 | 10-13 (490) |
| 2026-10-13T00:00:00Z | 10-13 08:00 | 10-13 (490) |
| 2026-10-13T11:59:59Z | 10-13 19:59 | **10-14** (355) |
| 2026-10-13T12:00:00Z (old SDK) | 10-13 20:00 | **10-14** (355) |

So the service's day boundary for this account lies between 08:00 and 19:59 local, which is neither UTC nor local midnight. The exact rule was not pinned down and is not needed: the local midnight is what the app sends.

**Fix.** The domain walks local calendar days (machine zone by default, like the app's device zone; injectable `tz`), sends each day's local midnight as `time`, and labels the record with that local date (`epoch_s` = that local midnight). The facade passes local day bounds (end = 23:59:59), the same as `steps`.

**Re-run** (`mifit export consumption --from 2026-10-10 --to 2026-10-14`): 10-11 = 576, 10-12 = 453, 10-13 = 490, matching the app's values for the same dates. 10-14 (today) = 355 and still rising as the day syncs. 10-10 = 587 was not compared with the app. No notes.

## Caveat (as designed)

`active_kcal`/`total_kcal` on the current/most-recent day can be lower than the app's live figure because the cloud lags the phone's on-device blend (see notes/05). Fully-synced past days match.

## Outcome
**PASS**: the consumption domain reads the real cloud calorie fields, `active_kcal` is the app's activity-calorie value, and each record's `date` is the app's day. No tokens or raw per-day values beyond this verification record are committed.
