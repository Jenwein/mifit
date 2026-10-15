# Live verification: consumption (calories) domain

*Readings removed on 2026-10-15 (notes/07 §5): values are recorded as match / differ, and dates are relative to the run day D (2026-10-14).*

**Status:** PASS (values and dates verified against the app). The date-label offset found in the first run is fixed and re-verified live (see *Date-label fix*).

Run 2026-10-14 against the user's real account (cached QR session), `mifit`/`MiFit().consumption(...)`.

## What was read (diet/user_consumption → resting/active/intake/total)

First run, **before** the date-label fix (SDK machine in CST/UTC+8): D-4…D were read, and every record was labeled one day early (SDK date X held the app's day X+1); see *Date-label fix* for the corrected run.

## Acceptance

- **active_kcal matches the app's calorie ring exactly** (app, user-read) on D-3…D-1, while D was still syncing. This is the number the app's "活动卡路里/calorie ring" shows. ✅ (So the earlier "calories mismatch" is fully explained and the real field is now located and read.)
- **resting_kcal** is constant and equals the app's 静息卡路里 (the profile BMR). ✅
- **intake_kcal** matches the app. ✅
- **total_kcal = resting + active** = the app's "总卡路里" definition (静息 + 活动). ✅

## Date-label fix (re-verified live 2026-10-14)

The first run labeled every record one day early for this UTC+8 account (SDK date X held the app's day X+1). A first fix (`03904b1`, rounding request bounds to the nearest UTC midnight) changed which days were requested but **not** the `time` sent for each day, so a re-run showed the same offset. It was a misdiagnosis.

**Root cause.** The domain sent `time` = UTC midnight + 12 h (20:00 local). The app sends the day's **local midnight**: `HeatViewModel.preloadWeekConsumption` builds `HeatConsumptionNewRequest((int)(TimeDateUtil.atStartOfDay(day) / 1000))`, and `atStartOfDay(LocalDate)` is `date.toDateTimeAtStartOfDay(DateTimeZone.forID(TimeZone.getDefault().getID()))`. The service files a `time` under the day the app would mean by it, not under its UTC day.

**Probe evidence** (same account, read-only, one call each; the answered day was identified from the returned `exercise`):

| `time` sent | local (UTC+8) | answered with app day |
|---|---|---|
| local midnight D-3 / D-2 / D-1 | 00:00 | D-3 / D-2 / D-1 |
| D-2 23:59:59Z | D-1 07:59 | D-1 |
| D-1 00:00:00Z | D-1 08:00 | D-1 |
| D-1 11:59:59Z | D-1 19:59 | **D** |
| D-1 12:00:00Z (old SDK) | D-1 20:00 | **D** |

So the service's day boundary for this account lies between 08:00 and 19:59 local, which is neither UTC nor local midnight. The exact rule was not pinned down and is not needed: the local midnight is what the app sends.

**Fix.** The domain walks local calendar days (machine zone by default, like the app's device zone; injectable `tz`), sends each day's local midnight as `time`, and labels the record with that local date (`epoch_s` = that local midnight). The facade passes local day bounds (end = 23:59:59), the same as `steps`.

**Re-run** (`mifit export consumption --from D-4 --to D`): D-3…D-1 match the app's values for the same dates. D (the run day) was lower than the app and still rising as the day synced. D-4 was not compared with the app. No notes.

## Caveat (as designed)

`active_kcal`/`total_kcal` on the current/most-recent day can be lower than the app's live figure because the cloud lags the phone's on-device blend (see notes/05). Fully-synced past days match.

## Outcome
**PASS**: the consumption domain reads the real cloud calorie fields, `active_kcal` is the app's activity-calorie value, and each record's `date` is the app's day. No tokens or per-day values are committed.
