# Live verification: sleep domain

*Readings removed on 2026-10-15 (notes/07 §5): values are recorded as match / differ, and dates are relative to the run day D (2026-10-14).*

**Status:** PASS. Totals, stage minutes, bed and wake times, naps and sleep heart rate match the app. The score can't be checked on this account because the app shows only a rating. No record is produced for a day without data.

Run 2026-10-14 against the user's real account (cached QR session, UTC+8), using `mifit export sleep --from D-3 --to D`. Plan: [2026-10-14-mifit-sleep-domain.md](../plans/2026-10-14-mifit-sleep-domain.md), Task 4.

## Compared with the app (user-read)

| Field (B) | D | D-1 | D-2 | App |
|---|---|---|---|---|
| `asleep_min` (day total) | match | match | match | ✅ The app's total sleep includes naps |
| Night `start`/`end` | match | match | none | ✅ |
| Night deep / light / awake (min) | match | match | none | ✅ |
| Nap session | match | match | match | ✅ The app shows naps as "小睡", on a separate time-range view |
| `avg_hr_bpm` | match | match | `None` | ✅ Matches the app's sleep average HR |
| `score` | present | present | present | ⚠ The app shows no number, only a rating ("一般"), apparently because a setting is off. Not verifiable here. |
| Day D-3 | no record: no data that day; the SDK invents no zeros | | | ✅ |

- **Stage timeline:** the items of the D night add up exactly to the reported deep, light and awake minutes. The timeline is consistent with the totals the user confirmed. A point-by-point visual check of the app's chart is optional and wasn't required for PASS.
- **SpO2** (`*_spo2_pct`) and the min/max heart rate weren't compared individually; the user reported "all data matches". They stay in B on the same evidence as `avg_hr_bpm`.
- **REM:** the device reports none (`rem_min` is `None`). The mapping of state 4 to `"rem"` rests on the APK constant only.

## Decisions taken from this run

- **`score` stays in B.** (superseded 2026-10-14, see below) Its meaning is fixed by the APK (`AllDaySleepReport.getScore` ↔ `sleep_score`) and by the name, but its value couldn't be seen in the app. The docstring treats 0 as "no score". Revisit if a user with scores visible finds a mismatch.
- **Naps are not labelled in B.** The app presents them separately as 小睡, but the rule it uses was not found. In the observed data, a nap is a session with no stage breakdown: stage minutes are `None` and `stages` is `[]` (fix `1655fc1`). Consumers can tell naps apart that way.
- **The day total includes naps,** as the app's does, so `asleep_min` is the figure the app shows.
- **Superseded (2026-10-14, sleep hardening):** `score` moved out of B; it is in `raw` only (`sleep_score`). The design §2 anti-goal wins over the name-based reading, and the score is also reported for a nap-only day, where its meaning in the app is unknown.

## Outcome

**PASS.** No tokens and no health values are committed.
