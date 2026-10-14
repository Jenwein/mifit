# mifit SDK — Foundation & Core Read Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `mifit` Python package's foundation and a verified read path that can log in by QR and read the user's own Xiaomi-cloud steps (daily-summary path) and heart-rate (raw-record path), exported via CLI.

**Architecture:** A thin ingestion stack (crypto → transport → auth/session) shared by all data domains, plus a small domain registry where each domain declares its endpoint/key and maps the faithful cloud JSON (A) into a neutral typed model (B). This plan ships the stack + two domains (steps via `get_aggregated_fitness_data_by_time`, heart-rate via `get_fitness_data_by_time`) to exercise both read paths; later plans add more domains on the same skeleton. Mi-specific ingestion is isolated so a future second provider is a mechanical move (design §12 lightweight reservation).

**Tech Stack:** Python 3.12+, `requests` (HTTP), `qrcode` (render login QR), stdlib `hashlib`/`base64`/`struct`/`json` (nonce/signed-nonce/RC4/SHA1 — RC4 hand-implemented, matches reference), `pytest` (tests). No AES in this plan (FDS is a later plan).

**Spec:** `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md`

## Global Constraints

- Python **3.12+**; dependencies minimal (`requests`, `qrcode`; dev: `pytest`).
- **Read-only.** No write/modify/delete endpoints are called anywhere in this plan.
- **Data source = Xiaomi cloud** `https://hlth.io.mi.com/` (region `cn` → no host prefix; other regions → `https://<region>.hlth.io.mi.com/`). Base path `app/v1/`. Service id (sid) = `miothealth`.
- **A-faithful primary, B-normalized for high-frequency domains.** B models use app-neutral names (no Mi-specific terms), live in `src/mifit/models/`, and never embed Mi wire field names. Any B field whose meaning is not confirmed against real data is omitted (design anti-goal).
- **Credentials never printed/logged/committed.** Session tokens (`serviceToken`, `ssecurity`, `cUserId`, `userId`, `region`) persist only to a local file under a `.gitignore`d path; file created with owner-only permissions. No real token or health value appears in repo, tests, or fixtures.
- **Judge HTTP success by HTTP status, not business `code`** (a signing/auth error returns HTTP 401 with business `code=0`; notes/04 §3).
- Throttling defaults (configurable): serial (no concurrency), ~1s inter-request delay with jitter, time-windowed pagination; bulk/long-range export off by default.
- Mi-specific ingestion (auth/crypto/transport/domains) stays under `src/mifit/` with a clear seam; `models/` is provider-neutral (design §12). Mark seam boundaries in comments.
- Signing algorithm (verified triple-source + live, notes/01,02,04): `signed_nonce = b64(SHA256(b64decode(ssecurity) + b64decode(nonce)))`; params RC4'd with `b64decode(signed_nonce)` as key after discarding 1024 keystream bytes, cipher state **continuous across sorted fields**; `signature = b64(SHA1("GET" + "&" + encodedPath + "&" + sorted("k=v") + "&" + signed_nonce))`; `rc4_hash__` is the same SHA1 over *plaintext* params, added before encryption; final query carries encrypted params + `signature` + `_nonce`; response body = `b64decode` → RC4 (fresh cipher, 1024 discard) → UTF-8 JSON. `filterSignatureKeys=["data"]` (only the `data` param is signed/encrypted).

## Review Focus

- **Expired/invalid session (HTTP 401):** transport must surface a typed `AuthError` (not a generic failure) so the CLI can tell the user to re-scan; 401 is ambiguous (expiry *or* signing bug) so the message must not claim expiry as fact. → tested in Task 4.
- **Empty / missing data vs zero:** a domain day with no records must return an empty result, never fabricated zeros; `null` fields stay `None`. → tested in Tasks 6 & 7.
- **Pagination that never terminates / drops the cursor:** the paging loop must stop only on `has_more == false`, pass the server's `next_key` unchanged, and guard against a repeated/empty cursor. → tested in Task 5.
- **Raw heart-rate incompleteness (known issue, notes/04 §4):** windowed raw queries can drop points; the HR domain must expose the result as "windowed, possibly incomplete" and offer the omit-`start_time` full-paginate read, not silently imply completeness. → tested in Task 7.
- **Region other than cn:** host rewrite must prefix `<region>.` and always send `region_tag`; a wrong/missing region must not silently fall back to cn data. → tested in Task 2.

---

### Task 1: Crypto primitives

**Files:**
- Create: `src/mifit/crypto.py`
- Test: `tests/test_crypto.py`

**Interfaces:**
- Produces:
  - `generate_nonce(time_diff_ms: int = 0) -> str`
  - `signed_nonce(ssecurity: str, nonce: str) -> str`
  - `rc4_crypt(key: bytes, data: bytes) -> bytes` (1024-byte discard built in)
  - `build_signature(method: str, path: str, params: dict[str, str], signed_nonce_value: str) -> str`
  - `encrypt_query_params(*, method: str, path: str, params: dict[str, str], nonce: str, ssecurity: str, signature_path: str | None = None) -> dict[str, str]`
  - `decrypt_response_payload(body: str, nonce: str, ssecurity: str) -> dict`

- [ ] **Step 1: Write failing tests** in `tests/test_crypto.py`:
  - `test_nonce_decodes_to_12_bytes`: `base64.b64decode(generate_nonce(0))` has length 12.
  - `test_signed_nonce_is_b64_of_32_bytes`: `len(base64.b64decode(signed_nonce(s, n))) == 32` for sample b64 `s`,`n`.
  - `test_rc4_known_answer`: `rc4_crypt(key, msg)` equals a golden vector (see Step 3 for oracle).
  - `test_encrypt_then_decrypt_roundtrip`: given fixed `ssecurity`/`nonce`, RC4-decrypting the encrypted `data` value reproduces the plaintext; `signature` and `_nonce` keys present.
  - `test_signature_is_stable_b64`: `build_signature` returns the same 28-char b64 SHA1 for identical inputs.
- [ ] **Step 2: Run tests, verify they fail** — `pytest tests/test_crypto.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `src/mifit/crypto.py`.** Port the verified algorithm from `reference/mi-fitness-sync/src_mi_fitness_sync_activity_crypto.py` (RC4 with 1024 discard + continuous state; `signed_nonce` = SHA256 over decoded ssecurity+nonce; SHA1 signature over `METHOD&path&sorted(k=v)&S`; `rc4_hash__` added pre-encryption; `_nonce`/`signature` appended). Keep the Global-Constraints algorithm exact. **Golden vectors for `test_rc4_known_answer` and the roundtrip:** generate them by running the independent reference oracle `C:\dev\mi-health-lab\evidence\tools\verify_protocol_08.py` (or `ProtocolReference08.java`) on fixed synthetic inputs and pin the outputs; cite the source in a comment. Do not use real credentials.
- [ ] **Step 4: Run tests, verify pass** — `pytest tests/test_crypto.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add src/mifit/crypto.py tests/test_crypto.py && git commit -m "feat(crypto): nonce/signed-nonce/RC4/signature primitives"`

---

### Task 2: Region resolution & host rewrite

**Files:**
- Create: `src/mifit/region.py`
- Test: `tests/test_region.py`

**Interfaces:**
- Produces:
  - `COUNTRY_CODE_TO_REGION: dict[str, str]`
  - `region_for_country_code(cc: str | None) -> str | None`
  - `regionalize(url: str, region: str) -> str` (cn → unchanged; else insert `<region>.` after scheme)
  - `BASE_HOST = "https://hlth.io.mi.com/"`, `BASE_PATH = "app/v1/"`, `SID = "miothealth"`

- [ ] **Step 1: Write failing tests** in `tests/test_region.py`:
  - `test_cn_host_unchanged`: `regionalize(BASE_HOST, "cn") == BASE_HOST`.
  - `test_non_cn_host_prefixed`: `regionalize(BASE_HOST, "de") == "https://de.hlth.io.mi.com/"`.
  - `test_country_code_maps`: `region_for_country_code("CN")=="cn"`, `("DE")=="de"`, `("US")=="us"`, `("SG")=="sg"`, `("IN")=="i2"`, `("RU")=="ru"`.
  - `test_unknown_country_code_returns_none`: `region_for_country_code("ZZ") is None`.
- [ ] **Step 2: Run tests, verify fail** — `pytest tests/test_region.py -v`.
- [ ] **Step 3: Implement `src/mifit/region.py`.** Copy the country→region table from `reference/mi-fitness-sync/src_mi_fitness_sync_activity_region_mapping.py` (247 entries; source: APK `assets/server_config/servers.json`). `regionalize` does a single `"://" → "://<region>."` replace for non-cn.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(region): country→region map and regional host rewrite"`

---

### Task 3: Session model & secure local store

**Files:**
- Create: `src/mifit/auth/session.py`, `src/mifit/paths.py`
- Test: `tests/test_session_store.py`
- Modify: `.gitignore` (add the credentials dir)

**Interfaces:**
- Produces:
  - `@dataclass Session{ user_id: str, c_user_id: str, service_token: str, ssecurity: str, region: str, created_at: str, extra_cookies: dict[str,str] }`
  - `Session.is_valid() -> bool` (service_token and user_id non-empty)
  - `save_session(s: Session, path: Path | None = None) -> Path`
  - `load_session(path: Path | None = None) -> Session | None`
  - `default_session_path() -> Path` (under a gitignored app-data dir)

- [ ] **Step 1: Write failing tests** in `tests/test_session_store.py`:
  - `test_save_then_load_roundtrip`: save a synthetic `Session`, load it, fields equal.
  - `test_load_missing_returns_none`.
  - `test_saved_file_is_owner_only` (POSIX: mode `0o600`; on Windows assert file exists and skip mode check).
  - `test_repr_redacts_secrets`: `repr(session)` contains neither `service_token` nor `ssecurity` values.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement.** `Session` as a dataclass with a custom `__repr__` that redacts `service_token`/`ssecurity`. Store = JSON to `default_session_path()`; create parent dir, set `0o600` where supported. Add the credentials dir to `.gitignore`.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(auth): Session model and secure local token store"`

---

### Task 4: Signed transport (single request)

**Files:**
- Create: `src/mifit/transport.py`, `src/mifit/errors.py`
- Test: `tests/test_transport.py`

**Interfaces:**
- Consumes: Task 1 crypto, Task 2 region, Task 3 `Session`.
- Produces:
  - `class AuthError(Exception)`, `class ApiError(Exception)` in `errors.py`
  - `class Transport(session: Session, *, http=None, delay_s: float = 1.0)`
  - `Transport.get_json(endpoint_path: str, data_obj: dict, *, sign_path: str | None = None) -> dict` — builds `data=json`, signs/encrypts (Task 1), sends GET with cookies (`serviceToken`,`cUserId`,`userId`,`locale`) + header `region_tag`, applies region host, raises `AuthError` on HTTP 401, `ApiError` on other non-2xx or decrypted `code != 0`, else returns decrypted payload dict.

- [ ] **Step 1: Write failing tests** in `tests/test_transport.py` using a fake HTTP (object with `.get(url, params, headers, timeout)` returning a canned response):
  - `test_request_is_signed_and_regionalized`: fake captures outgoing call; assert URL host (cn → `hlth.io.mi.com`), query keys `{data, rc4_hash__, signature, _nonce}`, cookie header contains `serviceToken=`/`cUserId=`, header `region_tag` present.
  - `test_http_401_raises_AuthError`: fake returns status 401 (even with body `code=0`) → `AuthError`; message does not assert expiry as certain.
  - `test_business_code_nonzero_raises_ApiError`: 200 + decrypted `{"code":1,"message":"x"}` → `ApiError` carrying code/message.
  - `test_success_returns_decrypted_payload`: 200 + RC4-encrypted `{"code":0,"data_list":[]}` → returns that dict.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `transport.py` + `errors.py`.** Inject `http` (default `requests.Session()`); apply `delay_s` sleep (skipped/patched in tests). Cookie assembly mirrors notes/04 §2. Decrypt via Task 1. Success check: HTTP status first, then `code`.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(transport): signed regional GET with 401/code handling"`

---

### Task 5: Paginated pull helper

**Files:**
- Modify: `src/mifit/transport.py`
- Test: `tests/test_pagination.py`

**Interfaces:**
- Consumes: Task 4 `Transport`.
- Produces: `Transport.get_paged(endpoint_path: str, data_obj: dict, *, list_key: str = "data_list", sign_path: str | None = None, max_pages: int | None = None) -> list[dict]` — repeatedly calls `get_json`, appending `payload[list_key]`, following `payload["next_key"]` while `payload["has_more"]` is true; stops on `has_more` false, on a `max_pages` cap, or if `next_key` repeats/empties while `has_more` stays true (guard).

- [ ] **Step 1: Write failing tests** in `tests/test_pagination.py` (fake Transport returning scripted pages):
  - `test_collects_all_pages_until_has_more_false`: 3 pages → concatenated list; `next_key` threaded unchanged into each subsequent request.
  - `test_stops_at_max_pages`: `max_pages=2` stops after 2 even if `has_more` true.
  - `test_guards_repeated_cursor`: `has_more` true but identical `next_key` → stops (no infinite loop), does not raise.
  - `test_empty_first_page_returns_empty_list`.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `get_paged`.**
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(transport): paginated pull with cursor guard"`

---

### Task 6: Steps domain (daily-summary path) + neutral model

**Files:**
- Create: `src/mifit/domains/base.py`, `src/mifit/domains/steps.py`, `src/mifit/models/steps.py`
- Test: `tests/test_domain_steps.py`, `tests/fixtures/steps_daily_sample.json`

**Interfaces:**
- Consumes: Task 5 `Transport.get_paged`.
- Produces:
  - `src/mifit/models/steps.py`: `@dataclass StepRecord{ date: str, steps: int, distance_m: int | None, calories: int | None, epoch_s: int }`
  - `src/mifit/domains/base.py`: `@dataclass DomainResult{ records: list, raw: list[dict], notes: list[str] }`; a `register(name)` decorator + `REGISTRY: dict[str, type]` (minimal — enough to list/dispatch domains).
  - `src/mifit/domains/steps.py`: `fetch_steps(t: Transport, start_epoch_s: int, end_epoch_s: int) -> DomainResult` → GET `app/v1/data/get_aggregated_fitness_data_by_time`, `data={tag:"daily_report", key:"steps", reverse:true, limit:50, start_time, end_time}`, list_key `data_list`; map each `value` (JSON string → `{time,steps,distance,calories}`) to `StepRecord`; `raw` holds the untouched parsed dicts.

- [ ] **Step 1: Write failing tests** in `tests/test_domain_steps.py`:
  - Create `tests/fixtures/steps_daily_sample.json` = a **synthetic** decrypted payload: `{"code":0,"has_more":false,"next_key":null,"data_list":[{"sid":"s","key":"steps","time":1759248000,"value":"{\"time\":1759248000,\"steps\":8000,\"distance\":5200,\"calories\":210}"}]}` (no real data).
  - `test_parses_steps_record`: with a fake Transport returning the fixture, `fetch_steps(...)` yields one `StepRecord(steps=8000, distance_m=5200, calories=210)` and `raw` preserves the parsed value dict.
  - `test_empty_day_yields_no_records`: empty `data_list` → `records == []`, no fabricated zero.
  - `test_null_fields_stay_none`: a value missing `distance`/`calories` → `distance_m is None`, `calories is None`.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `base.py`, `models/steps.py`, `domains/steps.py`.** `value` is a JSON string requiring a second `json.loads`. Keep wire names out of `StepRecord`. Confirm the envelope keys (`data_list`/`has_more`/`next_key`) and the value fields against `C:\dev\mi-health-lab` steps evidence while implementing; if the live/sample envelope differs, follow the real shape and update the fixture + a comment.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(domains): steps daily-summary domain + StepRecord model"`

---

### Task 7: Heart-rate domain (raw-record path) + completeness flag

**Files:**
- Create: `src/mifit/domains/heart_rate.py`, `src/mifit/models/heart_rate.py`
- Test: `tests/test_domain_heart_rate.py`, `tests/fixtures/hr_raw_sample.json`

**Interfaces:**
- Consumes: Task 5 `Transport.get_paged`.
- Produces:
  - `src/mifit/models/heart_rate.py`: `@dataclass HeartRatePoint{ epoch_s: int, bpm: int, kind: int | None }`
  - `src/mifit/domains/heart_rate.py`:
    - `fetch_heart_rate_window(t, start_epoch_s, end_epoch_s) -> DomainResult` → GET `app/v1/data/get_fitness_data_by_time`, `data={key:"heart_rate", start_time, end_time, reverse:true}`, list_key `data_list`; each `value` (JSON string → `{time,bpm,type}`) → `HeartRatePoint`. Adds a note: windowed query, may be incomplete (Review Focus / notes/04 §4).
    - `fetch_heart_rate_full(t, end_epoch_s, max_pages: int = 10) -> DomainResult` → same endpoint but **omit `start_time`**, keep `end_time`/`reverse`/`next_key`, paginate (candidate fuller read per notes/04 §4).

- [ ] **Step 1: Write failing tests** in `tests/test_domain_heart_rate.py`:
  - Create `tests/fixtures/hr_raw_sample.json` = synthetic `{"code":0,"has_more":false,"next_key":null,"data_list":[{"sid":"s","key":"heart_rate","time":1759248600,"value":"{\"time\":1759248600,\"bpm\":72,\"type\":null}"}]}`.
  - `test_parses_hr_point`: fake Transport returns fixture → one `HeartRatePoint(epoch_s=1759248600, bpm=72, kind=None)`; assert `bpm == 72` (no ×5 scaling — notes/04 §2).
  - `test_window_result_flags_possible_incompleteness`: `fetch_heart_rate_window(...).notes` contains a non-empty incompleteness note.
  - `test_full_read_omits_start_time`: with a capturing fake Transport, `fetch_heart_rate_full(...)` sends `data` without a `start_time` key but with `end_time`.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement.** No bpm scaling. `type` → `kind` (keep raw int/None, no interpretation). Window note copy fixed in code.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(domains): raw heart-rate domain with completeness flag"`

---

### Task 8: QR login + STS exchange

**Files:**
- Create: `src/mifit/auth/qr.py`
- Test: `tests/test_auth_qr.py`

**Interfaces:**
- Consumes: Task 1 crypto (STS clientSign is SHA1-based, distinct from request RC4), Task 3 `Session`, Task 2 region.
- Produces:
  - `request_qr_login(http=None, sid: str = "miothealth") -> QrChallenge{ login_url: str, qr_payload: str, lp_url: str }` → GET `https://account.xiaomi.com/longPolling/loginUrl?sid=<sid>&_json=true`.
  - `poll_qr_login(challenge, http=None) -> LoginResult{ pass_token, cUserId, ssecurity, nonce, location, user_id }` → GET `challenge.lp_url`; strip `&&&START&&&`; parse; surface `NeedNotificationException`-equivalent typed error when `securityStatus != 0`.
  - `exchange_sts(result, sid, http=None) -> Session` → `clientSign = b64nowrap(SHA1("nonce=" + decimal_nonce + "&" + ssecurity))`; GET `result.location` with `clientSign` + `_userIdNeedEncrypt=true`; read `<sid>_serviceToken` (fallback `serviceToken`) from cookies; build `Session` (region resolved via Task 2 from account/user_region, default `cn`).
  - `login_qr(http=None, on_qr=<callable(login_url,qr_payload)>) -> Session` orchestrates the three.

- [ ] **Step 1: Write failing tests** in `tests/test_auth_qr.py` (fake HTTP with scripted responses):
  - `test_request_qr_hits_longpolling_url`: asserts the GET URL + `sid=miothealth`.
  - `test_poll_strips_safe_prefix_and_parses`: body `"&&&START&&&{...}"` → fields extracted.
  - `test_client_sign_formula`: `exchange_sts` computes `clientSign == b64(SHA1("nonce=123&"+ssecurity))` for a fixed nonce/ssecurity (golden via Task 1 oracle).
  - `test_login_builds_valid_session`: end-to-end with scripted responses → `Session.is_valid()` true, secrets populated.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `qr.py`.** Port the longPolling/STS flow from `reference/mi-fitness-sync/src_mi_fitness_sync_auth_client.py` (STS section) adapted to the QR entry (`login-path-06.md` in mi-health-lab). STS `clientSign` uses raw `ssecurity` string (not the RC4-derived key). Do not log tokens.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(auth): QR login longPolling + STS exchange"`

---

### Task 9: Client facade

**Files:**
- Create: `src/mifit/client.py`, `src/mifit/__init__.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: Tasks 3,4,6,7,8.
- Produces:
  - `class MiFit(session: Session | None = None, *, http=None)` — loads stored session when none passed; raises `AuthError` if none/invalid.
  - `MiFit.steps(start, end) -> DomainResult`, `MiFit.heart_rate(start, end, *, full=False) -> DomainResult`, `MiFit.raw(key, start, end) -> list[dict]` (A passthrough), `MiFit.login_qr(on_qr=...) -> None` (logs in and stores session).
  - `src/mifit/__init__.py` exports `MiFit`, `Session`, errors, models.
  - Date args accept `"YYYY-MM-DD"` (local-tz day bounds → epoch seconds) or int epoch seconds.

- [ ] **Step 1: Write failing tests** in `tests/test_client.py` (inject a fake Transport/session):
  - `test_steps_delegates_to_domain`: returns the steps `DomainResult`.
  - `test_date_string_converts_to_epoch_day_bounds`: `"2026-10-01"` → start `00:00:00`, end `23:59:59` local.
  - `test_no_session_raises_AuthError`.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `client.py` + `__init__.py`.** Date parsing helper fixed here; delegates to domains with a shared `Transport`.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit** — `git commit -m "feat(client): MiFit facade"`

---

### Task 10: CLI (login + export)

**Files:**
- Create: `src/mifit/cli/__init__.py`, `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 9 `MiFit`.
- Produces:
  - `mifit login` → runs `MiFit.login_qr`, renders the QR to the terminal (via `qrcode`) and prints the login URL, stores the session, prints success without secrets.
  - `mifit export <domain> --from <date> --to <date> [--json <path>] [--full]` → calls the matching `MiFit` method, writes `{records, raw, notes}` as JSON (stdout or file). `<domain>` ∈ registered domains (`steps`, `heart_rate`).
  - `pyproject.toml` declares the package, deps, and console entry point `mifit = mifit.cli:main`.

- [ ] **Step 1: Write failing tests** in `tests/test_cli.py` (invoke `main([...])` with a patched `MiFit`):
  - `test_export_steps_writes_json`: `export steps --from 2026-10-01 --to 2026-10-02 --json out.json` writes parseable JSON with `records`/`raw`/`notes`.
  - `test_export_unknown_domain_errors`: unknown domain → non-zero exit, message lists valid domains.
  - `test_cli_never_prints_token`: on login success path (patched), stdout contains no token value.
- [ ] **Step 2: Run tests, verify fail.**
- [ ] **Step 3: Implement `cli/__init__.py` (`main(argv=None)`) + `pyproject.toml`.** Use `argparse`. `--full` only affects `heart_rate`.
- [ ] **Step 4: Run tests, verify pass; verify install** — `pip install -e . && mifit --help`.
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): login and export commands + packaging"`

---

### Task 11: Live verification (manual, gated on token — NOT in CI)

**Files:**
- Create: `docs/superpowers/verification/2026-10-14-core-read-live.md`

**Interfaces:** Consumes the whole package + a real QR session.

- [ ] **Step 1:** Document the procedure: run `mifit login` (user scans QR), then `mifit export steps --from <recent> --to <today>` and `mifit export heart_rate --from <day> --to <day>`.
- [ ] **Step 2:** Compare exported steps daily totals against the Mi Fitness app's day/week view (acceptance: equal within rounding, per design §8.1); compare against `C:\dev\mi-health-lab` verified samples where available.
- [ ] **Step 3:** For heart_rate, record whether the windowed vs `--full` read differ in point counts (the known incompleteness, notes/04 §4); note findings, do not block.
- [ ] **Step 4:** Record outcome (pass / partial / fail with evidence) in the verification doc. No tokens/health values committed.
- [ ] **Step 5: Commit** the verification doc — `git commit -m "docs: live verification of core read path"`

---

## Self-Review

**1. Spec coverage:** Python lib + CLI (Tasks 9,10) ✓; read-only cloud source, sid/host/region (Tasks 2,4) ✓; QR login primary (Task 8) ✓; A-passthrough (`raw`, `DomainResult.raw`) + B models (Tasks 6,7,9) ✓; secure token store (Task 3) ✓; signing/crypto verified (Task 1) ✓; pagination/throttle (Tasks 4,5) ✓; neutral models / seam (Tasks 6,7 + constraints) ✓; verification against app (Task 11, design §8.1) ✓. Deferred by scope (later plans, stated up top): sleep/spo2/weight/workout domains, FDS+AES, full-history, session auto-refresh, writes. Password-login fallback is noted in spec §11 but not built here (QR is primary; add in a later plan) — intentional scope cut, flagged.

**2. Step scan:** Each code step gives a signature + file; bodies appear only for the fixtures and the clientSign formula (values the implementer can't invent). No "handle edge cases" lines.

**3. Type consistency:** `DomainResult` (Task 6) reused by Tasks 7,9,10; `Transport.get_json`/`get_paged` (Tasks 4,5) consumed by 6,7; `Session` (Task 3) by 4,8,9; `AuthError` (Task 4) by 9,10. Names consistent.

**4. Review Focus:** 401 → Task 4; empty/zero → Tasks 6,7; pagination cursor → Task 5; HR incompleteness → Task 7; non-cn region → Task 2. All five owned.

**5. Proportion:** Plan is mostly signatures, test names, and fixed fixtures; code blocks limited to synthetic fixtures and the clientSign KAT. Proportional to a foundation+2-domain slice.
