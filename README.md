# mifit

[![CI](https://github.com/Jenwein/mifit/actions/workflows/ci.yml/badge.svg)](https://github.com/Jenwein/mifit/actions/workflows/ci.yml)

Read your own Mi Fitness (小米运动健康) data from the Xiaomi cloud: a small
Python library and a command-line tool.

The Mi Fitness app syncs your steps, heart rate, sleep and more to the Xiaomi
cloud, but Xiaomi offers no public API for individuals. mifit signs in to your
own account by QR code, reads that synced data and returns it as JSON or as
Python objects, so you can build on it.

> **Unofficial.** Not affiliated with, endorsed by or supported by Xiaomi.
> See [Disclaimer](#disclaimer).

## What it reads today

| Domain | What you get | `records` type | Checked against the app |
|---|---|---|---|
| `steps` | steps and distance per day | `StepRecord` | yes |
| `heart_rate` | raw heart-rate samples | `HeartRatePoint` | yes |
| `heart_rate_daily` | heart rate per day: average, maximum, minimum, resting | `HeartRateDailySummary` | pending |
| `consumption` | calories per day: resting, active, intake, total | `ConsumptionRecord` | yes |
| `sleep` | sleep per day: totals, stage minutes, sessions with their stage timeline | `SleepRecord` → `SleepSession` → `SleepStage` | yes; the sleep score could not be checked and is in `raw` only |
| `workouts` | one entry per workout: start and end, sport, duration, distance, active calories, average and maximum heart rate, steps | `Workout` | pending |
| `spo2_daily` | blood oxygen (SpO2) per day: average, maximum, minimum | `Spo2DailySummary` | pending |
| `spo2` | raw SpO2 readings | `Spo2Point` | pending |
| `weight` | weight entries: time, weight, BMI (body composition in `raw` only) | `WeightRecord` | pending |
| `profile` (its own command: `mifit profile`) | height, weight, daily step and calorie goals | `UserProfile` | pending |
| `raw` (Python only: `client.raw(key, ...)`) | the rows of any fitness-data key, as the service sends them | none: a list of the service's rows | not applicable: nothing is interpreted |

"Checked against the app" means the values were compared with what the Mi
Fitness app shows, on one real account (China region, UTC+8). "Pending" means
implemented and tested offline, but not yet compared with the app. More is
planned, such as workout details and GPS tracks. Meanwhile, `raw` returns the
rows of any other key of the fitness-data endpoint, uninterpreted.

## Install

Requires Python 3.12 or later.

```bash
git clone https://github.com/Jenwein/mifit.git
cd mifit
pip install -e .
```

## Log in

```bash
mifit login
```

A QR code is drawn in the terminal, with the login URL below it. Scan it with
the Xiaomi / Mi Fitness app on your phone (or open the URL) and approve the
sign-in. Your password never passes through mifit.

The session is saved to `~/.mifit/session.json` (on Windows
`%USERPROFILE%\.mifit\session.json`). It grants access to your Mi Fitness
account's health data (not only reading). It also holds your account's
passToken, which mifit uses to renew the session when it expires. The passToken
is account-level: it can obtain tokens for any Xiaomi service, so the file
grants access to your whole Xiaomi account, not just your Mi Fitness health
data, and it stays useful long after the session token itself has expired.
Treat it like a password, and do not share it or commit it. On Linux and macOS
the file is readable by you only; on Windows it relies on your user profile's
permissions.

If Xiaomi asks for an extra security confirmation, `mifit login` prints a URL:
open it, confirm, then run `mifit login` again.

## Export

```bash
mifit export <domain> --from YYYY-MM-DD --to YYYY-MM-DD [--json PATH] [--full] [--chunk day|week|month] [--long] [--resume]
```

- `<domain>`: `consumption`, `heart_rate`, `heart_rate_daily`, `sleep`,
  `spo2`, `spo2_daily`, `steps`, `weight` or `workouts`.
- `--from` / `--to`: first and last day, inclusive, as local calendar days.
- `--json PATH`: write to a file instead of standard output.
- `--full`: `heart_rate` only; page backwards through the history instead of
  querying one time window (see [Known limitations](#known-limitations)).
  This is a single read: it ignores `--from`, so it needs no `--long` however
  long the range, and it cannot be combined with `--chunk`, `--long` or
  `--resume`.
- `--chunk day|week|month`: the chunk size (1, 7 or 31 days) for a range
  longer than one chunk; the default depends on the domain.
- `--long`: allow a range over 92 days.
- `--resume`: continue an interrupted export from `PATH.partial` (needs
  `--json`).

```bash
mifit export sleep --from 2026-10-01 --to 2026-10-07 --json sleep.json
```

The output is one JSON object with three lists:

```json
{ "records": [], "raw": [], "notes": [] }
```

### Long ranges

A range longer than one chunk (7 days for raw heart rate and SpO2, 31 days
for everything else, counted from `--from`) is read chunk by chunk, one
request at a time, with progress on standard error. A shorter range is a
single read, as before. The chunks are merged into one output of the same
shape; with more than one chunk, each note starts with the chunk it came
from. Ranges over 92 days need `--long`: they take many requests and can run
for many minutes. The one exception is `heart_rate --full`: it ignores
`--from` and is a single read, never chunked, so it needs no `--long`.

```bash
mifit export heart_rate --from 2026-01-01 --to 2026-06-30 --long --json heart_rate.json
```

With `--json PATH`, progress is kept in `PATH.partial`: your exported data
plus the export's arguments (domain, dates, chunk size), no credentials.
Treat it like the output itself. If the export stops (a network error, an
expired session, Ctrl+C), the finished chunks stay in that file; rerun the
same command with `--resume` and only the missing chunks are read. The output
is the same as an uninterrupted run's, and the partial file is deleted once
it is written. An export to standard output is chunked the same way but
cannot be resumed.

A failed chunk stops the export; nothing is retried automatically. If one
chunk keeps failing, you can skip it by exporting the ranges before and after
it separately.

## Python API

```python
from mifit import MiFit

client = MiFit()  # uses the session saved by `mifit login`

steps = client.steps("2026-10-01", "2026-10-07")
for day in steps.records:
    print(day.date, day.steps, day.distance_m)

sleep = client.sleep("2026-10-01", "2026-10-07")
energy = client.consumption("2026-10-01", "2026-10-07")
heart = client.heart_rate("2026-10-07", "2026-10-07")      # one time window
heart_full = client.heart_rate("2026-10-07", "2026-10-07", full=True)

rows = client.raw("sleep", "2026-10-07", "2026-10-07")     # any fitness-data key, as served
```

Dates are `"YYYY-MM-DD"` strings (local calendar days, inclusive) or ints of
Unix seconds. Each domain method returns a result with `records`, `raw` and
`notes`; `raw(key, ...)` returns the service's rows as they came, for any key
of the fitness-data endpoint (`data/get_fitness_data_by_time`), not the daily
reports. Errors specific to mifit derive from `mifit.MiFitError`: `AuthError`
(no usable session, or HTTP 401), its subclass `RenewalError` (an HTTP 401 the
automatic renewal could not fix; log in again), `ApiError` (the service
refused the request or sent something unreadable) and
`NotificationRequiredError` (raised by the login when Xiaomi asks for a
security confirmation). Network failures surface as `requests` exceptions,
and a bad date argument raises `ValueError` or `TypeError`.

For a long range, `read_chunks` reads one chunk at a time (the CLI's chunk
sizes; `size="day"`, `"week"` or `"month"` overrides them) and `merge_chunks`
joins the parts. Its dates are `"YYYY-MM-DD"` strings only.

```python
from mifit.chunks import merge_chunks

parts = []
for chunk, part in client.read_chunks("heart_rate", "2026-01-01", "2026-06-30"):
    print(chunk.label, len(part.records))  # each chunk is read when the loop asks for it
    parts.append((chunk, part))
heart = merge_chunks(parts)  # its records are those of one read of the whole range
```

`read_chunks(..., skip=labels)` passes over the chunks whose labels you
already have, to resume. The library has no 92-day limit: a long range is
many requests, so ask for what you need.

When a request is refused with HTTP 401, the client renews the session once
from its passToken and sends the request again. A client that uses the
session saved by `mifit login` saves the renewed session back to
`~/.mifit`. Explicit sessions (`MiFit(session)`) are renewed in memory; read
`client.session` to keep the renewed one. `MiFit(auto_renew=False)` turns
renewal off, so a 401 raises `AuthError` straight away.

`MiFit(http=...)` injects the HTTP client, to keep a client offline. With
renewal on, the renewal runs on that client too, so its `get` must accept the
`cookies=` and `allow_redirects=` keywords as well as the usual `params=`,
`headers=` and `timeout=` (a `requests.Session` does). Without an injected
client, the renewal uses a fresh `requests.Session` of its own.

## Two layers: `raw` and `records`

- **`raw`** is the service's decoded payloads (the inner `value` of each row,
  or for consumption the response's `result` object), with Xiaomi's own field
  names. It is not a complete dump of every row the service sent.
- **`records`** are typed dataclasses with neutral names (`asleep_min`,
  `avg_hr_bpm`, ...). A field's meaning is established from what the app
  displays or from the app's own code before it enters this layer. One, the
  REM stage, rests on the app's code alone, because the author's device
  reports no REM. The sleep score is not in this layer: the app showed only a
  rating, so the number could not be checked; it is in `raw` as `sleep_score`.
  A value the service did not report is `None`, never a made-up `0`.
- **`notes`** are caveats about that particular result: skipped rows, data
  that may be incomplete.

## Known limitations

- **Cloud only, and the cloud lags.** mifit reads what the app has synced to
  the Xiaomi cloud. If cloud sync is off or has not run yet, data is missing
  or late; the latest day may be partial.
- **The calorie ring is synthesized by the app.** The app combines cloud
  activity with phone-side activity, so `consumption` for the latest day can
  read lower than the ring. `step_calories` in `steps` is the step report's
  walking-calorie figure, not the ring.
- **Raw heart-rate windows are corrected by the SDK.** The windowed
  heart-rate read asks the service for a day before the start and keeps only
  the requested window, because the service drops samples at a window's start
  (notes/08). `raw("heart_rate", ...)` returns the service's answer as it
  came, without this correction. `--full` (`full=True`) pages backwards from
  the end date instead; it ignores the start date and stops after 10 pages,
  saying so in `notes` when older data remains.
- **NaN and Infinity become `null`.** JSON has no such numbers, so where the
  service sends one, the CLI writes `null` and a note says how many it
  replaced.
- **A read the service breaks off says so.** When the service reports more
  pages but sends no usable cursor to fetch them, the read stops there and
  `notes` says that rows in the range may be missing.
- **Read-only.** mifit never writes, changes or deletes anything in your
  account.
- **Sessions expire.** On an HTTP 401, mifit renews the session once from the
  stored passToken and retries the request. When that fails, or the session
  was saved before renewal existed (it has no passToken), run `mifit login`
  again. How long sessions really last is still being observed: logins, 401s
  and renewals are logged (event, time and ages only) to
  `~/.mifit/session-log.jsonl`. (An HTTP 401 can also mean the request
  signature was refused; a 401 right after a renewal is taken to mean that and
  is not retried.)
- **Narrowly tested.** One account in the China region. Other regions are
  routed but unverified, and Xiaomi can change the protocol at any time.
- **Be gentle with the service.** Requests are sent one at a time with about a
  second's pause plus random jitter. Long ranges are read in chunks, and a
  range over 92 days needs `--long`. This lowers the risk of being
  rate-limited, but does not remove it.

## Development

```bash
pip install -e ".[test]"
python -m pytest -q
```

The tests are offline: a fake transport stands in for the Xiaomi cloud, and
any attempt to open a network connection fails the test. The `test` extra
includes `tzdata`, so the daylight-saving tests also run on Windows, which has
no time-zone database of its own. CI runs the suite on Linux with Python 3.12,
3.13 and 3.14.

## Disclaimer

mifit is an independent, unofficial project. It is not affiliated with,
endorsed by or supported by Xiaomi. "Xiaomi" and "Mi Fitness" are trademarks
of their owners and are used here only to say what the software works with.

It is meant for reading **your own** data with **your own** account.
Automated access may conflict with Xiaomi's terms of service and could get
your account rate-limited or restricted; use it at your own risk. Nothing it
outputs is medical advice. The software is provided "as is", without warranty
of any kind (see [LICENSE](LICENSE)).

## Credits

Request signing and encryption, the transport, the region table and the STS
token exchange are adapted from
[Mi-Fitness-Sync](https://github.com/kevinkwee/Mi-Fitness-Sync) by kevinkwee
(MIT License). See [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES).

## License

MIT. See [LICENSE](LICENSE).
