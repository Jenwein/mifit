# mifit T1 Compliance & Repo Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public repo legally and hygienically sound: an MIT `LICENSE`, a `THIRD_PARTY_NOTICES` plus per-file headers for the code adapted from kevinkwee/Mi-Fitness-Sync, a `README.md`, and a report-only audit of the whole git history for credentials and personal health data.

**Architecture:** No behavior change. Task 1 adds the two legal files, a comment header on each adapted source file, package license metadata, and one small offline test that keeps the notices list and the headers in sync. Task 2 writes the README from the current CLI and `MiFit` facade. Task 3 scans every commit (diffs, file names, commit messages) with a throwaway scanner kept outside the repo, writes findings to `notes/07-history-audit.md` by location only, and closes T1 in the roadmap. History is never rewritten.

**Tech Stack:** Python 3.12+, pytest, setuptools (PEP 639 license metadata), git. No new runtime deps.

**Spec:** roadmap `docs/superpowers/roadmap.md` §4 "T1 合规与仓库卫生" and the §2 row on missing attribution; design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` §5 (vendor, keep the MIT notice), §2 (anti-goals; read-only), §3 (credentials never committed; cloud lag; raw heart-rate completeness); `notes/02-reference-mi-fitness-sync.md` (upstream, MIT).

## Provenance findings (compared `src/mifit/` against `reference/mi-fitness-sync/`, 2026-10-14)

The local reference copy flattens upstream paths (`src_mi_fitness_sync_activity_crypto.py` = upstream `src/mi_fitness_sync/activity/crypto.py`). It has no `LICENSE` file; its README only says "licensed under the MIT License".

| mifit file | Adapted from (upstream path) | Evidence |
|---|---|---|
| `src/mifit/crypto.py` | `src/mi_fitness_sync/activity/crypto.py` | Same functions and structure (`signed_nonce`, RC4 with 1024-byte discard, `build_signature`, `generate_nonce`, `encrypt_query_params`, `decrypt_response_payload`); identical error message. |
| `src/mifit/transport.py` | `src/mi_fitness_sync/activity/transport.py` | `get_json` follows `request_json` (nonce, `data` param encryption, 401 / HTTP / `code` handling); same cookie header (`serviceToken`, `cUserId`, `userId`, `locale` from `uLocale`) and `region_tag` header. `get_paged` is mifit's own. |
| `src/mifit/region.py` | `src/mi_fitness_sync/activity/region_mapping.py`, and `_regionalize_endpoint` in `activity/transport.py` | `COUNTRY_CODE_TO_REGION` is identical (247 entries); `region_for_country_code`; `regionalize` = `_regionalize_endpoint`. |
| `src/mifit/auth/qr.py` | `src/mi_fitness_sync/auth/client.py` | STS exchange (`clientSign` = b64 SHA1 `nonce=<n>&<ssecurity>`, `_userIdNeedEncrypt`, `<sid>_serviceToken` then `serviceToken`, `Set-Cookie` fallback via `SimpleCookie`), `SAFE_PREFIX` `&&&START&&&` stripping. The QR long-polling flow is mifit's own (from the APK / notes/04). |

Not attributed (see Decisions): `auth/session.py`, `errors.py`, `client.py`, `cli/`, `domains/`, `models/`, `paths.py`; the test vectors in `tests/test_crypto.py`.

## Global Constraints

- Project license: MIT, `Copyright (c) 2026 Jenwein`.
- Upstream: name `Mi-Fitness-Sync`, URL `https://github.com/kevinkwee/Mi-Fitness-Sync`, MIT. Copyright line `Copyright (c) kevinkwee` unless Task 1 Step 3 finds upstream's exact line.
- No behavior change: source edits are comment headers only; the suite stays offline (`python -m pytest -q`; baseline today **267 passed, 2 skipped**).
- Repo is public: every committed byte is published at the next push. No credential value, account identifier or health figure goes into any new file or commit message. README examples use placeholder dates and show no output figures.
- Git history is read, never rewritten: no `filter-repo`, `rebase`, `reset` of pushed commits, or force-push. The audit only adds `notes/07-history-audit.md`.
- Line endings: LF in every new or modified file (`git ls-files --eol <file>` shows `i/lf w/lf`). Python writes use `newline="\n"`.
- Python header comments are ASCII only.

## Review Focus

- **Notices list and headers drift apart** (a file gets a header but is not listed, or is listed but loses its header): the test must fail in both directions. → Task 1 tests `test_every_listed_file_carries_the_header`, `test_every_file_with_the_header_is_listed`.
- **Header written as a string, displacing the module docstring** (`__doc__` becomes the attribution): the header must be `#` comments above the docstring. → Task 1 test `test_headers_keep_module_docstrings`.
- **Health figures outside `verification/`** (the roadmap's M3 targets, the sleep plan's real-night durations, readings in notes/04–06): the audit must read every committed `.md`/`.json`, not only `verification/*.md`. → Task 3 Step 6 check that the report's table lists every path from that enumeration.
- **History that is public but not local** (a remote-only branch, GitHub `refs/pull/*`): must be fetched and scanned. → Task 3 Step 1 `ls-remote` comparison.
- **The audit report itself leaking a value**: rerunning the scanner on the new files must find no value. → Task 3 Step 8.

---

### Task 1: LICENSE, third-party notices, attribution headers

**Files:**
- Create: `LICENSE`, `THIRD_PARTY_NOTICES`, `tests/test_licensing.py`
- Modify: `src/mifit/crypto.py`, `src/mifit/transport.py`, `src/mifit/region.py`, `src/mifit/auth/qr.py` (header only, above the docstring), `pyproject.toml`

**Interfaces:**
- Produces: `THIRD_PARTY_NOTICES` lists adapted files as lines matching `^  - (src/mifit/\S+\.py)$`; each such file's leading `#` block contains `adapted from Mi-Fitness-Sync` and `THIRD_PARTY_NOTICES`. Task 2's README links both files.

- [ ] **Step 1: Write the failing tests** in `tests/test_licensing.py` (`ROOT = Path(__file__).resolve().parents[1]`; `MARKER = "adapted from Mi-Fitness-Sync"`; helper `_leading_comments(path)` = the file's lines from the top while they start with `#`; helper `_listed()` = sorted matches of `^  - (src/mifit/\S+\.py)$` (re.M) in `THIRD_PARTY_NOTICES`):
  - `test_license_is_mit_for_jenwein_2026`: `LICENSE` text starts with `MIT License`, contains `Copyright (c) 2026 Jenwein` and `Permission is hereby granted, free of charge`.
  - `test_notices_carry_the_upstream_mit_license`: notices contain `https://github.com/kevinkwee/Mi-Fitness-Sync`, a line matching `^Copyright \(c\) \S` (re.M), `Permission is hereby granted, free of charge` and `THE SOFTWARE IS PROVIDED "AS IS"`.
  - `test_notices_list_the_adapted_files`: `_listed() == ["src/mifit/auth/qr.py", "src/mifit/crypto.py", "src/mifit/region.py", "src/mifit/transport.py"]`.
  - `test_every_listed_file_carries_the_header`: for each listed path, `MARKER` and `"THIRD_PARTY_NOTICES"` are in `"\n".join(_leading_comments(ROOT / path))`.
  - `test_every_file_with_the_header_is_listed`: `{p.relative_to(ROOT).as_posix() for p in (ROOT / "src/mifit").rglob("*.py") if MARKER in "\n".join(_leading_comments(p))} == set(_listed())`.
  - `test_headers_keep_module_docstrings`: for each listed file, `ast.get_docstring(ast.parse(text))` is truthy and does not contain `Mi-Fitness-Sync`.
- [ ] **Step 2: Run** `python -m pytest tests/test_licensing.py -v` → FAIL (`LICENSE` / `THIRD_PARTY_NOTICES` missing).
- [ ] **Step 3: Check upstream's copyright line (read-only).** Run `gh api repos/kevinkwee/Mi-Fitness-Sync/license --jq .content | python -c "import base64,sys; print(base64.b64decode(sys.stdin.read()).decode()[:200])"`. If it prints a `Copyright (c) ...` line, use that line verbatim in Step 4 in place of `Copyright (c) kevinkwee`. If the command fails (no network, not authenticated), keep the fallback and say so in the commit message body.
- [ ] **Step 4: Create `LICENSE` and `THIRD_PARTY_NOTICES`** with exactly this text (`MIT_BODY` = the three standard MIT paragraphs below, identical in both files):

`LICENSE`:
```text
MIT License

Copyright (c) 2026 Jenwein

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`THIRD_PARTY_NOTICES`:
```text
Third-party notices for mifit

mifit is licensed under the MIT License (see LICENSE). Parts of it are adapted
from the project below, which is distributed under its own license, reproduced
here as that license requires.

==============================================================================
Mi-Fitness-Sync
https://github.com/kevinkwee/Mi-Fitness-Sync
==============================================================================

Files in mifit that contain portions adapted from it:

  - src/mifit/crypto.py
      from src/mi_fitness_sync/activity/crypto.py: nonce, signed nonce, RC4
      with the 1024-byte discard, request signature, query-parameter
      encryption and response decryption.
  - src/mifit/transport.py
      from src/mi_fitness_sync/activity/transport.py: the signed GET request,
      cookie and region_tag headers, and HTTP 401 / HTTP error / business
      code handling.
  - src/mifit/region.py
      from src/mi_fitness_sync/activity/region_mapping.py and
      activity/transport.py: the country-code to region table and the
      regional host rewrite.
  - src/mifit/auth/qr.py
      from src/mi_fitness_sync/auth/client.py: the STS service-token exchange
      (clientSign, service-token cookie lookup) and response-prefix handling.

MIT License

Copyright (c) kevinkwee

<MIT_BODY>
```

- [ ] **Step 5: Add the header** as the first lines of each listed file, followed by one blank line, then the existing docstring (unchanged). `<UPSTREAM>` per file: `crypto.py` → `src/mi_fitness_sync/activity/crypto.py`; `transport.py` → `src/mi_fitness_sync/activity/transport.py`; `region.py` → `src/mi_fitness_sync/activity/region_mapping.py and activity/transport.py`; `auth/qr.py` → `src/mi_fitness_sync/auth/client.py`.

```python
# Portions of this file are adapted from Mi-Fitness-Sync
# (https://github.com/kevinkwee/Mi-Fitness-Sync),
# <UPSTREAM>,
# used under the MIT License. See THIRD_PARTY_NOTICES for the
# upstream copyright notice and license text.
```

- [ ] **Step 6: Package metadata** in `pyproject.toml`: `[build-system] requires = ["setuptools>=77"]`; under `[project]` add `license = "MIT"` and `license-files = ["LICENSE", "THIRD_PARTY_NOTICES"]`.
- [ ] **Step 7: Run** `python -m pytest tests/test_licensing.py -v` → 6 PASS; `python -m pytest -q` → 273 passed, 2 skipped; `python -m pip install -e .` succeeds and `python -c "from importlib.metadata import metadata as m; d = m('mifit'); print(d['License-Expression'], d.get_all('License-File'))"` prints `MIT ['LICENSE', 'THIRD_PARTY_NOTICES']`; `git ls-files --eol` on the new/changed files after `git add` shows `i/lf w/lf`.
- [ ] **Step 8: Commit** `git add LICENSE THIRD_PARTY_NOTICES tests/test_licensing.py pyproject.toml src/mifit/crypto.py src/mifit/transport.py src/mifit/region.py src/mifit/auth/qr.py` then `git commit -m "chore: MIT license, third-party notices and attribution headers"` (body: which copyright line was used and why; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).

---

### Task 2: README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: Task 1's `LICENSE`, `THIRD_PARTY_NOTICES`; `mifit` CLI (`login`, `export <domain> --from --to [--json PATH] [--full]`, domains = `REGISTRY` keys); `MiFit` methods `steps`, `heart_rate(..., full=False)`, `consumption`, `sleep`, `raw(key, start, end)`; `DomainResult(records, raw, notes)`; errors `MiFitError`, `AuthError`, `ApiError`.

- [ ] **Step 1: Write `README.md`** with exactly this content:

````markdown
# mifit

Read your own Mi Fitness (小米运动健康) data from the Xiaomi cloud: a small
Python library and a command-line tool.

The Mi Fitness app syncs your steps, heart rate, sleep and more to the Xiaomi
cloud, but Xiaomi offers no public API for individuals. mifit signs in to your
own account by QR code, reads that synced data and returns it as JSON or as
Python objects, so you can build on it.

> **Unofficial.** Not affiliated with, endorsed by or supported by Xiaomi.
> See [Disclaimer](#disclaimer).

## What it reads today

| Domain | What you get | `records` type |
|---|---|---|
| `steps` | steps and distance per day | `StepRecord` |
| `heart_rate` | raw heart-rate samples | `HeartRatePoint` |
| `consumption` | calories per day: resting, active, intake, total | `ConsumptionRecord` |
| `sleep` | sleep per day: totals, stage minutes, sessions with their stage timeline | `SleepRecord` → `SleepSession` → `SleepStage` |

Each domain has been checked against the app on one real account (China
region, UTC+8). More (workouts, SpO2, weight, ...) is planned.

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
`%USERPROFILE%\.mifit\session.json`). It grants read access to your health
data: do not share it or commit it. On Linux and macOS the file is readable by
you only; on Windows it relies on your user profile's permissions.

If Xiaomi asks for an extra security confirmation, `mifit login` prints a URL:
open it, confirm, then run `mifit login` again.

## Export

```bash
mifit export <domain> --from YYYY-MM-DD --to YYYY-MM-DD [--json PATH] [--full]
```

- `<domain>`: `steps`, `heart_rate`, `consumption` or `sleep`.
- `--from` / `--to`: first and last day, inclusive, as local calendar days.
- `--json PATH`: write to a file instead of standard output.
- `--full`: `heart_rate` only; page backwards through the history instead of
  querying one time window (see [Known limitations](#known-limitations)).

```bash
mifit export sleep --from 2026-10-01 --to 2026-10-07 --json sleep.json
```

The output is one JSON object with three lists:

```json
{ "records": [], "raw": [], "notes": [] }
```

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

rows = client.raw("sleep", "2026-10-07", "2026-10-07")     # any data key, as served
```

Dates are `"YYYY-MM-DD"` strings (local calendar days, inclusive) or ints of
Unix seconds. Each domain method returns a result with `records`, `raw` and
`notes`; `raw(key, ...)` returns the service's rows as they came. Errors derive
from `mifit.MiFitError`: `AuthError` (no usable session, or HTTP 401) and
`ApiError` (the service refused the request or sent something unreadable).

## Two layers: `raw` and `records`

- **`raw`** is the service's data, decrypted and decoded but otherwise
  untouched: Xiaomi's own field names, nothing dropped.
- **`records`** are typed dataclasses with neutral names (`asleep_min`,
  `avg_hr_bpm`, ...). A field enters this layer only after its meaning has been
  checked against the app; anything unverified stays in `raw`. A value the
  service did not report is `None`, never a made-up `0`.
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
- **Raw heart rate may be incomplete.** A windowed heart-rate query can
  silently miss samples (cause unknown); every such result carries a note.
  `--full` (`full=True`) pages backwards from the end date instead; it ignores
  the start date and stops after 10 pages without a warning.
- **Read-only.** mifit never writes, changes or deletes anything in your
  account.
- **Sessions expire.** How long a session lasts is not known yet and there is
  no automatic renewal. When a command fails with an authorization error, run
  `mifit login` again. (An HTTP 401 can also mean the request signature was
  refused.)
- **Narrowly tested.** One account in the China region. Other regions are
  routed but unverified, and Xiaomi can change the protocol at any time.
- **Be gentle with the service.** Requests are sent one at a time with about a
  second's pause; long ranges are not split up yet, so export a few weeks at a
  time.

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
````

- [ ] **Step 2: Check it against the code.** `mifit export --help` lists exactly `steps, heart_rate, consumption, sleep` (any order); every `MiFit` method and field name used above (`steps`, `sleep`, `consumption`, `heart_rate(full=)`, `raw`, `date`, `steps`, `distance_m`, `asleep_min`, `avg_hr_bpm`, `step_calories`) exists (`grep` in `src/mifit/client.py` and `src/mifit/models/`); the 10-page cap matches `max_pages: int = 10` in `src/mifit/domains/heart_rate.py`. Fix the README, not the code, on any mismatch.
- [ ] **Step 3: Run** `python -m pytest -q` → 273 passed, 2 skipped (unchanged); `git ls-files --eol README.md` after `git add` → `i/lf w/lf`.
- [ ] **Step 4: Commit** `git add README.md` then `git commit -m "docs: README"` (trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).

---

### Task 3: Git history audit (report only) and roadmap close-out

**Files:**
- Create: `notes/07-history-audit.md`
- Modify: `docs/superpowers/roadmap.md` (§0 T1 row, §1 test count, §2 attribution row, §4 T1 result)
- Not committed: the scanner script, kept in the session scratchpad (outside the repo)

**Interfaces:**
- Consumes: Tasks 1–2 committed (so the scan covers them).
- Produces: `notes/07-history-audit.md` with sections 1–5 below. Locations are `short SHA` + path + line only; no matched value is ever written down.

- [ ] **Step 1: Fix the scope.** `git fetch origin --prune`; `git ls-remote origin`. For every remote SHA, `git cat-file -e <sha>^{commit}`; if any is missing locally (e.g. a `refs/pull/*`), `git fetch origin <ref>:refs/audit/<n>` so `--all` includes it. Record `git rev-list --all --count` and the ref list for the report. At the end of the task, `git update-ref -d` every `refs/audit/*`.
- [ ] **Step 2: File names ever committed.** `git log --all --name-only --pretty=format: | sort -u`. Flag any path matching: `session.json`, `*.session.json`, `.mifit/`, `secrets/`, `private/`, `evidence/`, `target/`, `reference/`, `work/`, `.env`, `*.har`, `*.pcap*`, `*.db`, `*.sqlite*`, `*.apk`, `*.dex`, `*.log`, `*cookie*`, `*token*`. (At planning time: none; 65 paths under `docs/`, `notes/`, `src/`, `tests/`, `tools/`, plus `.gitignore`, `pyproject.toml`; this plan's commit adds 1 and Tasks 1–2 add 4, so expect 70.)
- [ ] **Step 3: Write and run the scanner** (scratchpad, Python, `-I`). It parses `git log --all -m -p --text --unified=0 --no-color --no-ext-diff --format=commit:%H`: tracks the commit, the file (`+++ b/<path>`) and the new-file line from each `@@ … +c[,d] @@`, and tests every added line (`+`, not `+++`); it also tests `git log --all --format=commit:%H%n%B` (commit messages). Output per hit: `<short sha> <path or "message">:<line> <pattern> len=<n>` and never the matched text. Patterns (Python `re`; `\|` in the table is a plain `|` in the regex):

  | name | regex | catches |
  |---|---|---|
  | `cred_assign` | `(?i)\b(service_?token\|pass_?token\|ssecurity\|c_?user_?id\|user_?id\|device_?id\|clientsign\|step1token)\b["']?\s*[:=]\s*["']?([A-Za-z0-9+/=_%.-]{6,})` | a credential name with a value |
  | `cookie_value` | `(?i)\b(set-)?cookie\b["']?\s*[:=]\s*["']?[^"'\n]*=[^;"'\s]{6,}` | cookie headers with values |
  | `url_secret` | `(?i)[?&](servicetoken\|passtoken\|ssecurity\|clientsign\|_ssign\|sign\|nonce\|auth_key)=` | URLs carrying a secret parameter |
  | `ssecurity_shape` | `(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{22}==(?![A-Za-z0-9+/=])` | 16-byte base64 values |
  | `long_b64` | `(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}` | token-like base64 runs |
  | `session_json` | `"(service_token\|ssecurity\|c_user_id)"\s*:\s*"[^"]{6,}"` | a serialized mifit session |
  | `email` | `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}` | email addresses |
  | `phone_cn` | `(?<!\d)1[3-9]\d{9}(?!\d)` | CN mobile numbers |
  | `mac` | `(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b` | device MAC addresses |

- [ ] **Step 4: Classify every hit** by opening it locally (never copying the value anywhere): `测试合成值` (under `tests/`, placeholder-looking), `代码标识符` (a variable or parameter, not a value), `公开 app 常量` (e.g. the region-by-IP `auth_key` in notes/02, which is in the APK and the public upstream repo), `提交署名` (the `Co-Authored-By` trailer), or `疑似真实凭证/标识`. The first four are reported as counts per path; every `疑似真实凭证/标识` hit is reported individually with commit/path/line.
- [ ] **Step 5: Commit metadata.** `git log --all --format='%an <%ae>|%cn <%ce>' | sort | uniq -c`: report the number of distinct identities and, for each, only its kind (GitHub noreply / project alias / personal address) and commit count, not the address.
- [ ] **Step 6: Personal health figures.** Enumerate every `.md` and `.json` ever committed (`git log --all --name-only --pretty=format: -- '*.md' '*.json' | sort -u`). Read each one's current version and its history (`git log --all -p -- <path>`). For each, record: personal health figures present (yes / no), kinds (steps, distance, HR, sleep, calories, SpO2, dates of readings), introducing commits. `docs/superpowers/verification/*.md` contain the user's readings by design: list them as `个人健康数字存在（按设计）`. Fixtures under `tests/fixtures/` are meant to be synthetic: confirm (e.g. `"sid": "synthetic-sid"`); if `$HOME/mifit-verify/` exists, check whether any fixture row's decoded `value` object equals an entry of a `raw` list in those exports, and report only match / no match. Check: the report's §3 table has one row per enumerated path.
- [ ] **Step 7: Write `notes/07-history-audit.md`** (Chinese, like notes/01–06) with this skeleton:

```markdown
# 07 git 历史审计（T1；只报告，未改写任何历史）

- 日期：2026-10-14
- 范围：<N> 个提交（`git rev-list --all --count`）；refs：<列表>；远端对照：<一致 / 仅远端存在的 ref 及处理>
- 方法：文件名清单 + 扫描全部提交的新增行与提交信息（模式见附录）+ 逐个阅读曾提交的 .md/.json
- 结论：<一句话：是否发现真实凭证；个人健康数字分布在哪些文件>
- 本文不含任何匹配到的值，只给"提交 / 文件 / 行"。

## 1. 曾提交过的文件名
| 模式 | 命中 | 位置 |

## 2. 凭证与账号标识
| 类别 | 判定 | 位置（提交 / 文件 / 行）或计数 | 严重度 | 建议 |
严重度：严重（真实凭证，需要你注销会话）/ 高（真实账号标识、邮箱、手机号）/ 信息

## 3. 个人健康数字
| 文件 | 有无 | 类别 | 引入提交 | 说明 |

## 4. 提交元数据
| 身份类型 | 提交数 |

## 5. 待你决定
<逐条：发现 → 可选做法（保留 / 后续提交里删改 / 改写历史并强推，后者会改变所有提交 SHA）；本里程碑不做任何改写>

## 附录：扫描模式
<Step 2 的文件名模式与 Step 3 的正则表，原样>
```

- [ ] **Step 8: Leak self-check.** Rerun the scanner's patterns over the working-tree files `notes/07-history-audit.md`, `README.md`, `THIRD_PARTY_NOTICES`: the only allowed hits are the regex literals in the report's appendix. `git diff --check` is clean.
- [ ] **Step 9: Close T1 in the roadmap** (`docs/superpowers/roadmap.md`):
  - §0 T1 row, last cell → `✅ 完成（2026-10-14）；历史审计见 notes/07（只报告，未改写历史）`.
  - §1 → replace `测试 235 个通过` with the current count (`273 个通过、2 个跳过`, or what `python -m pytest -q` prints).
  - §2 attribution row → `| ~~移植自 kevinkwee/Mi-Fitness-Sync（MIT）的签名/传输逻辑没有保留署名；仓库无 LICENSE、无 README~~ → **已修（T1）**：LICENSE（MIT）、THIRD_PARTY_NOTICES、4 个源文件头署名、README。 | — | ✅ T1 |`.
  - §4 T1 → append `- **结果（2026-10-14）✅**：<one line: files added; audit conclusion; number of items left for the user in notes/07 §5>`.
- [ ] **Step 10: Run** `python -m pytest -q` → unchanged count; delete `refs/audit/*` if created; commit `git add notes/07-history-audit.md docs/superpowers/roadmap.md` then `git commit -m "docs(notes): git history audit (report only); roadmap T1 done"` (trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Do not push; the controller pushes after review.

---

## Decisions

1. **Four files are attributed** (`crypto.py`, `transport.py`, `region.py`, `auth/qr.py`), from code comparison (table above). `auth/qr.py` is included although its QR flow is original, because its STS exchange and response handling follow upstream closely; over-attributing costs nothing.
2. **Not attributed:** `auth/session.py` (only the idea of a local token cache was reused, per design §5; upstream's `auth/state.py` is not in the local copy and our store is written independently), `errors.py` (a generic message/code/payload exception), and everything under `client.py`, `cli/`, `domains/`, `models/`, `paths.py`. The vectors in `tests/test_crypto.py` were produced by running upstream code; they are data, not code, and are not listed.
3. **mi-health-lab is not third-party:** notes/03 records it as the user's own parallel investigation (Codex, `C:\dev\mi-health-lab`), so it gets no notice.
4. **Upstream copyright line:** the local copy has no `LICENSE`, so the fallback is `Copyright (c) kevinkwee`. Task 1 Step 3 tries a read-only `gh api` lookup of upstream's license and uses its exact line when reachable. Headers do not restate the copyright line, so only `THIRD_PARTY_NOTICES` would change.
5. **Headers are `#` comments above the docstring**, uniform 5 lines naming the upstream file; the full copyright and license live in `THIRD_PARTY_NOTICES` only.
6. **Package metadata:** `license = "MIT"` and `license-files` (PEP 639; setuptools floor raised to 77) so built wheels carry `LICENSE` and `THIRD_PARTY_NOTICES`, which MIT requires of copies.
7. **README in English** (public repo; the design fixes no language). It shows no personal figures and documents only what exists today.
8. **The audit runs last**, so it also covers T1's own commits before anything is pushed. Its scanner stays out of the repo; the report's appendix records the patterns so it can be rerun. The report is in Chinese, like notes/01–06.
9. **Health figures outside `verification/`** (roadmap M3 targets, the sleep plan's real-night durations, readings in notes/04–06) are reported for the user to decide on, like `verification/*.md`. Nothing is redacted or rewritten in T1.
10. **Test baseline is 267 passed, 2 skipped**, not 235 as roadmap §1 says. Task 1 adds 6 tests; Task 3 corrects the roadmap count.

## Self-Review

1. Spec coverage: LICENSE MIT (Task 1), MIT attribution in file headers + `THIRD_PARTY_NOTICES` (design §5, roadmap T1; Task 1), README with install / login / export / API / A-B / limitations / disclaimer / license (roadmap T1, design §2–§3; Task 2), history check for sessions, tokens and health data (roadmap T1; Task 3), roadmap §2 row closed (Task 3 Step 9). ✓
2. Step scan: each step names the file, the exact text or pattern, and the command with its expected output. ✓
3. Consistency: marker `adapted from Mi-Fitness-Sync`, list format `^  - (src/mifit/\S+\.py)$`, and the four paths match across the provenance table, Task 1 Steps 1/4/5, and Task 3 Step 9. ✓
4. Review Focus: five lines, each tied to a test (Task 1) or a check (Task 3). ✓
5. Proportion: three tasks; long blocks are only fixed copy (license texts, README, report skeleton). ✓
