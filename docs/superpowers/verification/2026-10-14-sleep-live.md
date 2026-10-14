# Live verification: sleep domain

**Status:** PASS. Totals, stage minutes, bed and wake times, naps and sleep heart rate match the app. The score can't be checked on this account because the app shows only a rating. No record is produced for a day without data.

Run 2026-10-14 against the user's real account (cached QR session, UTC+8), using `mifit export sleep --from 2026-10-11 --to 2026-10-14`. Plan: [2026-10-14-mifit-sleep-domain.md](../plans/2026-10-14-mifit-sleep-domain.md), Task 4.

## Compared with the app (user-read)

| Field (B) | 10-14 | 10-13 | 10-12 | App |
|---|---|---|---|---|
| `asleep_min` (day total) | 462 (7h42m) | 423 | 49 | ✅ The app's total sleep includes naps (10-14 shows 7h42m) |
| Night `start`/`end` | 00:09–08:11 | 01:26–08:05 | none | ✅ |
| Night deep / light / awake (min) | 105 / 326 / 51 | 88 / 307 / 4 | none | ✅ |
| Nap session | 13:15–13:46 | 13:13–13:41 | 13:40–14:29 | ✅ The app shows naps as "小睡", on a separate time-range view |
| `avg_hr_bpm` | 63 | 65 | `None` | ✅ App sleep average HR: 63 and 65 |
| `score` | 60 | 70 | 40 | ⚠ The app shows no number, only a rating ("一般"), apparently because a setting is off. Not verifiable here. |
| Day 10-11 | no record | | | ✅ The user didn't wear the watch; the SDK produces no record and invents no zeros |

- **Stage timeline:** the 18 items of the 10-14 night add up to exactly the reported deep, light and awake minutes (105, 326 and 51). The timeline is consistent with the totals the user confirmed. A point-by-point visual check of the app's chart is optional and wasn't required for PASS.
- **SpO2** (`*_spo2_pct`) and the min/max heart rate weren't compared individually; the user reported "all data matches". They stay in B on the same evidence as `avg_hr_bpm`.
- **REM:** the device reports none (`rem_min` is `None`). The mapping of state 4 to `"rem"` rests on the APK constant only.

## Decisions taken from this run

- **`score` stays in B.** Its meaning is fixed by the APK (`AllDaySleepReport.getScore` ↔ `sleep_score`) and by the name, but its value couldn't be seen in the app. The docstring treats 0 as "no score". Revisit if a user with scores visible finds a mismatch.
- **Naps are not labelled in B.** The app presents them separately as 小睡, but the rule it uses was not found. In the observed data, a nap is a session with no stage breakdown: stage minutes are `None` and `stages` is `[]` (fix `1655fc1`). Consumers can tell naps apart that way.
- **The day total includes naps,** as the app's does, so `asleep_min` is the figure the app shows.

## Outcome

**PASS.** No tokens and no health values beyond this record are committed.
