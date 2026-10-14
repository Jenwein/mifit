# mifit T2 Session Lifetime & Automatic Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the health API answers HTTP 401, renew the service token once from the stored passToken (as the app does) and retry, without a QR scan. Record login and renewal times so the real session lifetime can be observed. Fall back to a clear `mifit login` message when renewal is impossible or does not help.

**Architecture:** `mifit login` keeps the `passToken` it already receives (today `exchange_sts` drops it), plus a random `device_id` sent as the `deviceId` cookie to the account service. `auth/renew.py::renew_session` repeats the app's `XMPassport.loginByPassToken`, which is `GET account.xiaomi.com/pass/serviceLogin` with `userId`/`passToken`/`deviceId` cookies. It parses the result with the same code as the QR long poll and finishes with the existing `exchange_sts`. `Transport.get_json` calls an injected `renew` hook on a 401 at most once per request and at most 3 times per transport. It retries only when the token actually changed and stops renewing after a renewal fails to cure a 401. `MiFit` supplies the hook. For a session that came from `~/.mifit` it saves the renewed session atomically and appends timestamp-only events to `~/.mifit/session-log.jsonl`, the lifetime record that auto-renewal would otherwise hide.

**Tech Stack:** Python 3.12+, `requests` (existing), pytest with fake HTTP objects. No new dependencies.

**Spec:** roadmap `docs/superpowers/roadmap.md` §4 T2 ("过期时自动续期一次；续不了再提示扫码。401 也可能是签名错误，不能无限重试") and §2 row "会话多久过期、能否用 passToken 自助续期，都未验证". Design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md`: §3 (credentials stored locally and owner-only, never committed; "对 401 做有限次 token 刷新重试（≤3 次）"; low frequency), §5 (reuse the reference's "本地缓存 + 过期刷新" idea), §7 ("复用缓存、仅在过期时刷新或要求重新登录"), §11. Evidence: `notes/04` §1/§3/§4, `notes/02`, `reference/mi-fitness-sync/docs_mi-fitness-auth-findings.md` and `src_mi_fitness_sync_auth_client.py`, and the static findings below.

## Facts checked at plan time (2026-10-14; static only, no live call made)

- **Stored session (field names only):** `c_user_id, created_at, extra_cookies{cUserId, userId}, region, service_token, ssecurity, user_id`. It has no passToken and no device id, so **this session cannot be renewed**. Sessions saved after Task 1 can be. `created_at` (UTC, `…Z`) is the login time and matches the file mtime, 2026-10-14 11:18:19 local.
- **Observed lifetime so far:** logged in 03:18Z, and the session still worked at about 10:20Z on the same day, so it lasts **at least 7 h**. No expiry has been seen yet.
- **APK 3.59.1, decompiled by method with `tools/rea.sh inspect-android-method` (output kept in scratch, not committed):**
  - `fitness.account.token.VerifyToken.onHttpResponseBefore`: on 401 it retries at most 3 times (`mCount >= 3` returns the 401). It retries only if the rebuilt request differs from the refused one. `cookie(..., refreshToken=true)` force-refreshes only when the cached token equals the token that was just refused.
  - `MiAccountInternalManager.getServiceToken(sid, refresh)` calls `XiaomiAccountManager.getServiceToken`, then `refreshServiceToken`.
  - `passport.accountmanager.OwnAppXiaomiAccountAuthenticator.getAuthTokenBundle` does the refresh. It builds `PassTokenLoginParams{userId, passToken, serviceId}` and calls `AccountHelper.getServiceTokenByPassToken`. With no passToken it shows the login UI. On `InvalidCredentialException` it clears the credential and asks for a login. A passToken that comes back is written back (`updateAccountInfo`).
  - `AccountHelper.getServiceTokenByPassToken` adds `deviceId` (the hashed device id) and `uDevId`, then calls `XMPassport.loginByPassToken`.
  - `XMPassport.loginByPassToken` sends `GET https://account.xiaomi.com/pass/serviceLogin` with params `sid`, `_json=true`, `_locale` and cookies `userId`, `passToken`, `deviceId` (plus optional `uDevId`, `pass_o`, `userSpaceId`). The body goes through `processLoginContent`, the same parser `loginByLongPolling` uses for the QR poll:
    - `code≠0` is an error (`70016` = invalid credential with meta-login data).
    - `securityStatus≠0` means a notification is needed.
    - `userId`/`passToken` come from the JSON or from the response headers/cookies (which one depends on `isResultFromCA`).
    - `ssecurity`/`nonce` come from the JSON or from `Extension-Pragma`.
    - `location` goes to `getServiceTokenByStsUrl`, the same STS exchange that `exchange_sts` implements.
  - `XMPassport.refreshPassToken` (`/pass/login/passtoken/refresh`) needs a device `fid` signature. It cannot be reproduced and is not used.
- **Conclusion:** renewal looks feasible from userId + passToken (+ deviceId), which is exactly what the app does for its own account. notes/04's "续期依赖账号 SDK" means it depends on the passToken that the account SDK keeps; serviceToken + ssecurity alone indeed cannot renew. This is **not live-verified**. Still unknown: whether the passToken is bound to the deviceId, how long the passToken lives, and whether expiry and signature 401s can be told apart from the response.
- **Prior art (vendored reference, MIT):** it sends `deviceId` = `secrets.token_hex(8).upper()` with `serviceLogin`, and reads `passToken`/`cUserId` from the JSON, then cookies, then headers.

## Global Constraints

- Branch from `main` after M3 (`m3-hr-daily`) is merged. Baseline: `python -m pytest -q` gives about 301 passed, 0 skipped with `.[test]` installed. Record the exact number at the start; no existing test is deleted.
- The health API stays read-only. Renewal calls only `https://account.xiaomi.com/pass/serviceLogin` and the https STS `location` it returns.
- **Secrets:** `pass_token`, `service_token`, `ssecurity` and cookie values never appear in `repr`, exception messages, CLI output or the session log. `device_id` is redacted in `repr` too. The session log holds only event names, UTC times and ages in seconds.
- Renewal policy (design §3): at most 1 renewal per request, at most 3 per `Transport`, and never proactively (only on a 401).
- Tests are offline (the conftest network guard), use synthetic values only, and **never touch the real `~/.mifit`** (Task 3 adds home isolation).
- No live auth, login or renewal call happens before Task 5. Task 5 backs up `~/.mifit/session.json` first.
- Repo files are LF. If a file is written by a script on Windows, use `newline="\n"` (memory: Windows `write_text` CRLF).
- Commit per task; push only after the user says yes.

## Review Focus

- **A session saved before T2** (no `pass_token`/`device_id`/`renewed_at`) must still load and read while its token is valid. On a 401 it must say "log in once to enable renewal", not raise `KeyError`/`TypeError`. Tests: Task 1 `test_load_session_saved_before_renewal_fields_existed`, Task 2 `test_session_without_pass_token_raises_before_any_request`, Task 3 `test_describe_session_without_pass_token`.
- **A signature 401, not expiry:** renewal succeeds but the retry is still 401. This must not loop, and must not renew again on every page of a long export. Tests: Task 4 `test_401_after_renewal_raises_and_never_renews_again`, `test_renewals_are_capped_per_transport`.
- **A non-cn account:** the STS answer carries no region hint, so a naive rebuild would default to `cn`. Renewal must keep the stored region. Test: Task 2 `test_renew_keeps_region_login_time_and_device_id`.
- **passToken leaks:** through `repr`, errors, the session log, CLI stderr, or a redirect that carries the cookie elsewhere. Tests: Task 1 `test_repr_redacts_pass_token_and_device_id`; Task 2 `test_renewal_errors_never_contain_secrets` and the `allow_redirects is False` assertion; Task 3 `test_record_event_appends_one_json_line_without_secrets`.
- **Writes to the user's real `~/.mifit`:** by tests, or by a library client built from an explicit session that would overwrite the stored session with another account's. Tests: Task 3 conftest `_isolated_home`; Task 4 `test_renew_hook_does_not_write_for_an_explicit_session`.

---

### Task 1: The session keeps what renewal needs

**Files:**
- Modify: `src/mifit/auth/session.py`, `src/mifit/auth/qr.py`
- Test: `tests/test_session_store.py`, `tests/test_auth_qr.py` (`FakeHttp.get` gains `cookies=None` and records it)

**Interfaces:**
- Produces (`session.py`):
  - `Session(user_id, c_user_id, service_token, ssecurity, region, created_at, extra_cookies={}, pass_token: str = "", device_id: str = "", renewed_at: str | None = None)`.
  - `Session.can_renew() -> bool` is true iff `pass_token` and `user_id` are non-empty.
  - Docstrings: `created_at` is the **login** time (UTC ISO-8601 `Z`) and renewal keeps it. `renewed_at` is the time of the last automatic renewal (`None` = never).
  - `load_session` requires only the original six fields; the three new ones default as above.
  - `repr` redacts `pass_token` and `device_id` and shows `renewed_at`.
- Produces (`qr.py`):
  - `new_device_id() -> str` returns 16 uppercase hex characters (`secrets.token_hex(8).upper()`, as the reference does).
  - `request_qr_login(http=None, sid=SID, *, device_id: str | None = None)` and `poll_qr_login(challenge, http=None, *, timeout_s=..., device_id: str | None = None)`. With a `device_id`, the GET carries `cookies={"deviceId": device_id}`; without one, no `cookies` kwarg is passed (`_get` gains an optional `cookies` parameter).
  - `exchange_sts(result, sid, http=None, *, device_id: str = "") -> Session` sets `pass_token=result.pass_token` and `device_id=device_id`. The STS request itself is unchanged and sends no cookies.
  - `login_qr` makes one `new_device_id()` per login and passes it to both QR requests and to `exchange_sts`.

- [ ] **Step 1: Write failing tests.**
  - `test_session_store.py`:
    - `test_roundtrip_keeps_renewal_fields`: saving and loading a session with `pass_token="SYNTH-PASS-TOKEN"`, `device_id="SYNTHDEVICE00001"`, `renewed_at="2026-10-15T00:00:00Z"` gives back an equal session.
    - `test_load_session_saved_before_renewal_fields_existed`: a file with only the 7 original keys loads, with `pass_token == ""`, `device_id == ""`, `renewed_at is None` and `not can_renew()`.
    - `test_repr_redacts_pass_token_and_device_id`: neither value is in `repr`/`str`/`format`; the `renewed_at` value is.
    - `test_can_renew_needs_pass_token_and_user_id` (parametrized).
  - `test_auth_qr.py`:
    - `test_sts_session_keeps_the_pass_token_and_device_id`: `session.pass_token == SYNTH_PASS_TOKEN`, `session.device_id == "SYNTHDEVICE00001"`, `http.calls[0].cookies is None`.
    - `test_login_qr_sends_one_device_id_on_both_account_requests`: `calls[0].cookies == calls[1].cookies == {"deviceId": session.device_id}`, `re.fullmatch("[0-9A-F]{16}", session.device_id)`, `calls[2].cookies is None`.
    - `test_new_device_id_is_random_16_hex`: two calls differ, and both match the regex.
    - `test_request_qr_without_device_id_sends_no_cookies`.
- [ ] **Step 2: Run** `python -m pytest tests/test_session_store.py tests/test_auth_qr.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the fields, `can_renew`, the `load_session` defaults, the `repr`, `new_device_id`, the `device_id` keywords and `login_qr` threading.
- [ ] **Step 4: Run** both files, then `python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(auth): keep passToken and a device id in the session"`

---

### Task 2: `renew_session` — passToken → serviceLogin → STS

**Files:**
- Create: `src/mifit/auth/renew.py`, `tests/test_auth_renew.py`, `notes/09-session-lifetime-renewal.md`
- Modify: `src/mifit/auth/qr.py` (extract the shared parser), `src/mifit/errors.py`, `src/mifit/__init__.py`, `tests/test_client.py::test_package_exports`

**Interfaces:**
- Consumes: Task 1's `Session` fields and `exchange_sts(..., device_id=)`.
- Produces:
  - `errors.RenewalError(AuthError)` with `__init__(self, message: str, *, code: int | None = None)` and `.code`. Docstring: the session expired or was refused and could not be renewed automatically; log in again. Exported as `mifit.RenewalError`.
  - `qr._parse_login_content(response, what: str, *, known: dict[str, str] | None = None) -> LoginResult` is extracted from `poll_qr_login`; `poll_qr_login` calls it with `known=None`, and its behavior and messages are unchanged. Its rules are the app's `processLoginContent`:
    - Check the code (`ApiError` with `.code`) and `securityStatus` (`NotificationRequiredError`).
    - Read `userId`/`passToken`/`cUserId` from the JSON, then the response cookies (`_response_cookies`), then `known[<wire name>]`.
    - Read `ssecurity`/`nonce` from the JSON, then `Extension-Pragma`, and `location` from the JSON.
    - If a field is still missing, raise `ApiError` naming the field only.
  - `renew.RENEW_URL = "https://account.xiaomi.com/pass/serviceLogin"`.
  - `renew_session(session: Session, http: Any = None, *, sid: str = SID) -> Session`:
    - Request: `GET RENEW_URL`, `params={"sid": sid, "_json": "true"}`, `cookies={"userId": user_id, "passToken": pass_token, "deviceId": device_id}` (`deviceId` left out when empty), `timeout=30`, `allow_redirects=False`.
    - Parsing: `_parse_login_content` with `known` = the stored `userId`/`passToken`/`cUserId`.
    - Result: `exchange_sts(result, sid, http, device_id=session.device_id)` and then `dataclasses.replace(..., region=session.region, created_at=session.created_at, pass_token=result.pass_token, renewed_at=<UTC now ISO Z>)`.
    - Errors become `RenewalError`, always raised `from None`: no `pass_token` (raised before any request), a non-2xx status (3xx included), an `ApiError` (keep `.code`), `NotificationRequiredError`, and an `exchange_sts` failure. Messages state the reason only (e.g. "Xiaomi refused the stored passToken (code 70016)."; "This session has no passToken (it was saved before automatic renewal existed).").
    - `requests` exceptions propagate unchanged.
    - Module docstring cites `XMPassport.loginByPassToken` and notes/09. The app's `_locale`/`uDevId` are not sent.

- [ ] **Step 1: Write failing tests** in `tests/test_auth_renew.py`. The fake HTTP follows `test_auth_qr.py` and also records `params`, `cookies` and `allow_redirects`. The `serviceLogin` body is `&&&START&&&{code:0, userId, passToken, cUserId, ssecurity:NEW, nonce, location, securityStatus:0}`, and the STS cookie is `miothealth_serviceToken=NEW`.
  - `test_renew_calls_service_login_with_pass_token_cookies`: checks the URL, params and cookies as above and `allow_redirects is False`. Call 2 goes to the `location` with `clientSign` computed from the **new** nonce and ssecurity.
  - `test_renew_returns_a_session_with_the_new_token_and_ssecurity`.
  - `test_renew_keeps_region_login_time_and_device_id`: old `region="de"`, `created_at="2026-10-14T03:18:19Z"`. Both are kept, `renewed_at.endswith("Z")`, and `extra_cookies` are the new STS cookies.
  - `test_renew_stores_a_rotated_pass_token`; `test_renew_keeps_the_old_pass_token_when_none_comes_back`; `test_renew_reads_pass_token_and_user_id_from_cookies` (the JSON lacks them and `Set-Cookie` has them).
  - `test_renew_falls_back_to_extension_pragma_for_ssecurity_and_nonce`.
  - `test_session_without_pass_token_raises_before_any_request`: `RenewalError` and `http.calls == []`.
  - `test_refused_pass_token_raises_renewal_error_with_code`: a body with `code: 70016` gives `RenewalError` with `.code == 70016`, `isinstance(exc, AuthError)`, and exactly 1 call (no STS).
  - `test_security_confirmation_raises_renewal_error`: `securityStatus: 1` gives `RenewalError`, not `NotificationRequiredError`, and no STS call.
  - `test_redirect_or_http_error_raises_renewal_error` (parametrized: 302, 403, 500).
  - `test_sts_failure_raises_renewal_error` (the STS response has no token cookie).
  - `test_renewal_errors_never_contain_secrets`: for every failure above, neither the old or new pass token, service token nor ssecurity is in `str(exc)`/`repr(exc)`.
  - `test_network_errors_propagate`: a fake raising `requests.ConnectionError` raises that same error.
  - Also add `RenewalError` to `test_package_exports`.
- [ ] **Step 2: Run** `python -m pytest tests/test_auth_renew.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `RenewalError`, the parser extraction and `renew_session`. Run `tests/test_auth_qr.py` unchanged as the regression check for the extraction.
- [ ] **Step 4: Write `notes/09-session-lifetime-renewal.md`** (Chinese, like notes/04 and notes/06):
  - §1 what the session stores and why the pre-T2 session cannot renew;
  - §2 the APK chain (the facts above, with class and method names);
  - §3 the request and response mapping `renew_session` implements;
  - §4 unknowns (deviceId binding, passToken lifetime, telling expiry and signature 401s apart);
  - §5 lifetime observations (≥ 7 h on 2026-10-14) and how to read `session-log.jsonl` (Task 3). Tasks 3–5 append to it.
- [ ] **Step 5: Run** `python -m pytest -q`. Expected: all pass.
- [ ] **Step 6: Commit** `git commit -m "feat(auth): renew the service token from the stored passToken"` (code + notes/09).

---

### Task 3: Lifetime observation (session ages, event log, 401 message)

**Files:**
- Create: `src/mifit/auth/lifetime.py`, `tests/test_lifetime.py`
- Modify: `src/mifit/paths.py`, `src/mifit/client.py` (`session` property only), `src/mifit/cli/__init__.py`, `tests/conftest.py`, `tests/test_cli.py`, `tests/test_client.py`

**Interfaces:**
- Consumes: Task 1's `Session.created_at`/`renewed_at`/`can_renew()`.
- Produces:
  - `paths.SESSION_LOG_FILE_NAME = "session-log.jsonl"` and `paths.default_session_log_path() -> Path`, which is `app_data_dir() / SESSION_LOG_FILE_NAME`.
  - `lifetime.session_ages(session, now: datetime | None = None) -> tuple[int | None, int | None]` returns `(login_age_s, token_age_s)`. The token age counts from `renewed_at`, or from `created_at` when there was no renewal. An unparsable time gives `None`, and a negative age is clamped to 0.
  - `lifetime.format_age(seconds: int | None) -> str` returns `"unknown"`, `"45m"`, `"7h02m"` or `"2d03h"` (under 1 h: minutes; under 1 d: `{h}h{m:02d}m`; otherwise `{d}d{h:02d}h`).
  - `lifetime.describe_session(session, now=None) -> str` is the exact copy `"logged in {created_at} ({age} ago); "` + (`"last renewed {renewed_at} ({age} ago)"` | `"never renewed"`) + `"; "` + (`"automatic renewal available"` | ``"automatic renewal unavailable (no passToken: run `mifit login` once to enable it)"``).
  - `lifetime.record_event(event: str, session: Session, *, now: datetime | None = None, path: Path | None = None) -> None` appends one line `{"event", "at", "login_age_s", "token_age_s"}` (`at` = UTC ISO `Z`). It creates the parent directory (`0o700`) and swallows `OSError`. The events used are `login`, `unauthorized`, `renewed` and `renew_failed`.
  - `MiFit.session` (read-only property) returns the session in use.
  - CLI:
    - `_cmd_login` calls `record_event("login", session)` after a successful save. `record_event` is imported into `mifit.cli` and looked up at call time, like `save_session`.
    - When `_cmd_export` catches an `AuthError` after a client was built, it prints `session: <describe_session(client.session)>` between the `error:` line and the "Run `mifit login`" line.
  - `conftest.py` gets the autouse fixture `_isolated_home(monkeypatch, tmp_path)`, which sets `HOME` and `USERPROFILE` to `tmp_path / "home"`.

- [ ] **Step 1: Write failing tests.**
  - `test_lifetime.py`:
    - `test_ages_from_login_and_renewal`: `created_at="2026-10-14T03:18:19Z"` with now `10:20:19Z` gives `(25320, 25320)`; with `renewed_at="2026-10-14T09:20:19Z"` it gives `(25320, 3600)`.
    - `test_unparsable_time_gives_unknown_age`.
    - `test_format_age` (parametrized: `None→"unknown"`, `0→"0m"`, `2700→"45m"`, `25320→"7h02m"`, `183600→"2d03h"`).
    - `test_describe_session_never_renewed`, `test_describe_session_renewed`, `test_describe_session_without_pass_token`.
    - `test_record_event_appends_one_json_line_without_secrets`: two events give two lines whose key set is exactly `{"event","at","login_age_s","token_age_s"}`, and the file text contains none of the token, ssecurity, pass token, user ids or device id.
    - `test_record_event_never_raises` (`path` is a directory).
    - `test_default_session_log_path_is_next_to_the_session`.
  - `test_cli.py`:
    - `test_login_records_a_login_event` (spy on `cli.record_event`).
    - `test_export_auth_error_prints_session_age`: `FakeMiFit` gets a `session` attribute and `steps` raising `AuthError`. stderr has `"session: logged in 2026-10-14T00:00:00Z"` before ``"Run `mifit login`"``, and no token.
    - `test_export_without_session_prints_no_session_line`.
  - `test_client.py`: `test_session_property_returns_the_session_in_use`.
- [ ] **Step 2: Run** the three files. Expected: FAIL.
- [ ] **Step 3: Implement** `paths`, `lifetime`, the property, the CLI lines and the conftest fixture.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass. A fresh run must create no new file under the real `~/.mifit` (`dir "$HOME\.mifit"` before and after).
- [ ] **Step 5: Commit** `git commit -m "feat(auth,cli): record session login/renewal times and show the session age on 401"`

---

### Task 4: Renew once on 401 (transport hook + facade wiring)

**Files:**
- Modify: `src/mifit/transport.py`, `src/mifit/client.py`, `README.md`, `tests/test_transport.py`, `tests/test_client.py` (existing `Transport` factories gain `renew=None`; `test_transport_is_built_from_the_session_and_injected_http` expects the hook)

**Interfaces:**
- Consumes: Task 2's `renew_session` and `RenewalError`; Task 3's `record_event` and `MiFit.session`.
- Produces:
  - `Transport(session, *, http=None, delay_s=1.0, timeout_s=30.0, renew: Callable[[Session], Session] | None = None, max_renewals: int = 3)`, plus a `Transport.session` property. `get_json` sends through a new `_send(endpoint_path, data_obj, sign_path) -> tuple[response, nonce]` (sign, delay, GET) and decrypts with the current session's `ssecurity`. With `renew=None`, behavior is unchanged, including the existing 401 message. The 401 rule:

```python
response, nonce = self._send(endpoint_path, data_obj, sign_path)
if response.status_code == 401 and self._renew is not None and self._renewals_left > 0:
    self._renewals_left -= 1
    old = self._session
    try:
        new = self._renew(old)
    except RenewalError as exc:
        raise RenewalError(f"Request was rejected with HTTP 401 and the session could not be renewed: {exc}", code=exc.code) from None
    if new.service_token == old.service_token:
        self._renewals_left = 0
        raise AuthError(_SAME_TOKEN_401)   # "...HTTP 401 ... renewing returned the same token..."
    self._session = new
    response, nonce = self._send(endpoint_path, data_obj, sign_path)   # fresh nonce, new ssecurity/cookies
    if response.status_code == 401:
        self._renewals_left = 0
        raise AuthError(_STILL_401)        # "...HTTP 401 again right after the session was renewed, so the request signature was probably refused..."
if response.status_code == 401:
    raise AuthError(<existing message>)
```

  - `MiFit(session=None, *, http=None, auto_renew: bool = True)`.
    - The client is *store-backed* when `session` was `None` (loaded from `~/.mifit`) or after `login_qr`.
    - `_make_transport(session)` builds `Transport(session, http=self._http, renew=self._renew if auto_renew else None)`; `__init__` and `login_qr` both use it.
  - `MiFit._renew(old) -> Session`:
    1. Record `unauthorized`.
    2. Call `renew_session(old, http=self._http)`. On any exception, record `renew_failed` and re-raise.
    3. Record `renewed` and set `self._session = new`.
    4. If store-backed, `save_session(new)`. An `OSError` there gives `warnings.warn("The renewed session could not be saved (...); the next run will renew again.", RuntimeWarning)` and the read continues.
    5. Return `new`.
    - Events and saves happen **only** when store-backed, and `login_qr` also records `login`.
  - README:
    - Login section: the session file now also holds the account passToken that renews it; treat it like a password.
    - Python API: `RenewalError` (an `AuthError`), `auto_renew=False`, and "explicit sessions are renewed in memory; read `client.session` to keep the renewed one".
    - Limitation "Sessions expire" becomes: on a 401 mifit renews once from the stored passToken and retries; when that fails, or the session predates renewal, run `mifit login`; lifetimes are being observed in `~/.mifit/session-log.jsonl`.

- [ ] **Step 1: Write failing tests.**
  - `test_transport.py` uses a `ScriptedHttp` whose steps are `(status, payload, ssecurity)`. Each body is encrypted with the request's own `_nonce` and that step's ssecurity.
    - `test_401_renews_once_and_retries_with_the_new_session`: the result is the payload, the hook ran once with the old session, there were 2 GETs, the 2nd `Cookie` has the new token, the two `_nonce` values differ, and `transport.session is new`. The body is encrypted with the NEW ssecurity, so it decrypts only if the new one was used.
    - `test_401_after_renewal_raises_and_never_renews_again`: steps 401, 401, then 401. The first call raises `AuthError` mentioning "401" and "signature" after 1 hook call. The second call raises with no further hook call and makes exactly 1 more GET.
    - `test_renewal_that_returns_the_same_token_does_not_retry`: 1 GET.
    - `test_renewal_failure_raises_renewal_error_mentioning_401`: `.code` is kept, it is an `AuthError`, and there was 1 GET.
    - `test_renewals_are_capped_per_transport`: four 401 episodes, each followed by 200. Three `get_json` calls succeed; the fourth raises after exactly 3 hook calls.
    - `test_paged_read_renews_mid_way`: page 1 OK (`has_more`, `next_key`), page 2 gets 401 then 200. Items come from both pages and the hook ran once.
  - `test_client.py`:
    - `test_transport_gets_the_renew_hook_unless_auto_renew_is_off`: keywords are `{"http": http, "renew": client._renew}`; with `auto_renew=False`, `renew` is `None`.
    - `test_renew_hook_persists_and_logs_for_a_stored_session`: patch `load_session`, `renew_session`, `save_session` and `record_event`. Then `saved == [new]`, the events are `["unauthorized", "renewed"]`, and `client.session is new`.
    - `test_renew_hook_does_not_write_for_an_explicit_session`: no save and no event.
    - `test_failed_renewal_is_logged_and_re_raised`: the events are `["unauthorized", "renew_failed"]`.
    - `test_save_failure_after_renewal_warns_and_keeps_going` (`pytest.warns(RuntimeWarning)`).
    - `test_login_qr_keeps_the_renew_hook_and_becomes_store_backed`.
    - `test_end_to_end_401_is_renewed_offline`: real `Transport` + real `renew_session`, with a fake HTTP that routes by URL: `hlth.io.mi.com` answers 401 and then 200 encrypted with the new ssecurity; `RENEW_URL` answers the login JSON; the STS `location` answers the token cookie. `MiFit(old_with_pass_token, http=fake).raw("steps", 1, 2)` returns the rows and `client.session.service_token == NEW`.
- [ ] **Step 2: Run** the two files. Expected: FAIL.
- [ ] **Step 3: Implement** the transport rule, the facade wiring and the README text.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: all pass, about 50 more than the baseline (parametrized cases included).
- [ ] **Step 5: Commit** `git commit -m "feat(transport,client): renew the session once on HTTP 401 and retry"`

---

### Task 5: Live check (manual, needs the user; not CI) + roadmap

**Files:**
- Create: `docs/superpowers/verification/<run date>-session-renewal-live.md`
- Modify: `notes/09-session-lifetime-renewal.md` (live result), `docs/superpowers/roadmap.md` (§0 T2 row, §2 session row, §4 T2 `结果`), and `README.md` only if the live result contradicts it

- [ ] **Step 1: Back up first** (outside the repo): `Copy-Item "$HOME\.mifit\session.json" "$HOME\.mifit\session.pre-t2.json"`. Then `pip install -e ".[test]"` and `python -m pytest -q` (all green).
- [ ] **Step 2: Last data point for the old session:** `mifit export steps --from <D> --to <D> --json "$HOME\mifit-verify\t2-before.json"`. Record whether it succeeds and its age (`describe_session` via the CLI on failure, or `created_at`). This gives a lower bound on the lifetime (or the first observed expiry).
- [ ] **Step 3: The user runs `mifit login`** and scans the QR code. Check by field names only: `python -I -c "import json,pathlib;d=json.loads((pathlib.Path.home()/'.mifit'/'session.json').read_text());print(sorted(d), bool(d.get('pass_token')), bool(d.get('device_id')))"`. If the QR login fails with the new `deviceId` cookie, record that, drop the cookie from the QR requests (decision 3 fallback), and log in again.
- [ ] **Step 4: Baseline:** the same export succeeds, and `session-log.jsonl` has only `login`.
- [ ] **Step 5: Provoke one renewal.** Replace the stored token with an invalid one; nothing is printed:
  `python -I -c "import dataclasses as d;from mifit.auth.session import load_session as l,save_session as s;s(d.replace(l(),service_token='t2-invalid'))"`
  Run the export again. Expected: it succeeds; the log gains `unauthorized` then `renewed`; `renewed_at` is set; `created_at` is unchanged.
  Run the export a third time. Expected: no new events.
  - If renewal fails, record the `RenewalError` reason and code only.
  - If the invalid token does not produce 401, record the status the service gave instead.
  - Either way, run `mifit login` again to restore a working session. The backup is kept until the user deletes it.
- [ ] **Step 6: Write it up.**
  - The verification doc records pass or fail per step, with no values.
  - notes/09 §5 gets the result and the lifetime lower bound.
  - Roadmap: T2 ✅ (or "partial: renewal refused, see notes/09"); the §2 row becomes "renewal verified/refused; natural lifetime ≥ N h, being observed via session-log". Natural expiry gets recorded later from the first `unauthorized` event's `token_age_s`.
  - `python -m pytest -q`, then commit `docs: session renewal live check; roadmap T2`. Push only after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Renewal mechanism.** Renewal uses the app's own path: passToken → `serviceLogin` → STS (`OwnAppXiaomiAccountAuthenticator` → `loginByPassToken`). It does not use `/pass/login/passtoken/refresh`, which needs a device `fid` signature. Research found this feasible, so the plan does more than the observation-only fallback. The fallback (lifetime log, clear 401 text, notes/09) is still delivered and is what remains if Task 5 shows renewal is refused.
2. **The passToken is stored in `session.json` by default.** It is an account-level credential, so a leaked file now matters for longer. That is the price of unattended use (design §7 "仅在过期时刷新"). The file stays owner-only, and the README says to treat it like a password. There is no opt-out flag (YAGNI); `auto_renew=False` exists only for library callers.
3. **One random `device_id` per login** (16 uppercase hex characters, as in the reference). It is sent as the `deviceId` cookie on the QR `loginUrl`, the long poll and `serviceLogin`, but not on STS or the health API, because the account SDK always sends one and a renewal should present the same device as the login did. Fallback if the QR login breaks: drop it from the QR requests (Task 5 Step 3).
4. **Times.** `created_at` stays the login time; there is no new `login_time` field, because the existing file already holds it and renaming would break loading. A new `renewed_at` is added. Renewal keeps `created_at`, `region` and `device_id`, takes a returned passToken (rotation) and otherwise keeps the old one.
5. **Retry policy.** At most 1 renewal per request and at most 3 per `Transport` (design §3, the app's `RETRY_MAX_NUM = 3`). A retry happens only if the token changed (as `VerifyToken` does). A 401 right after a renewal is treated as a signature problem and switches renewal off for that transport.
6. **Lenient field sourcing.** `userId`/`passToken`/`cUserId` are read from the JSON, then cookies, then the stored values. The app's account SDK reads one or the other depending on `isResultFromCA`; the STS exchange is the real gate.
7. **`allow_redirects=False`** on `serviceLogin`, because `requests` would carry dict cookies, the passToken among them, to a redirect target on another host. A 3xx response is a `RenewalError`.
8. **Store-backed rule.** Persistence and the event log happen only when `MiFit` read its session from `~/.mifit` or logged in itself. An explicit session renews in memory and is exposed via `MiFit.session`. A failed save after a successful renewal warns and does not fail the read.
9. **`RenewalError` subclasses `AuthError`,** so every existing `except AuthError` (the CLI included) still ends in "Run `mifit login`".
10. **The session log** (`~/.mifit/session-log.jsonl`: event, time, ages) is the lifetime observation channel, because auto-renewal hides expiry from the user. It never raises and never stores identifiers.
11. **Not done (YAGNI):** proactive or time-based renewal, a `mifit renew`/`mifit session` command, sending `_locale`/`uDevId`, password login (T5). The current pre-T2 session cannot renew, so one `mifit login` after T2 ships is required (Task 5 does it).
12. **The live test provokes a 401** by replacing the stored token, after a backup, instead of waiting an unknown time for natural expiry. Natural expiry is recorded later from the log.
13. **Notes number 09.** 07 is T1's audit and 08 is M3's.

## Self-Review

1. **Spec coverage:**
   - Roadmap T2 "记录每次登录时间和首次 401 的时间": `created_at`/`renewed_at` (Task 1) and the session log plus the age on 401 (Task 3).
   - "能否用 passToken 重新走 STS": researched above, implemented in Task 2, live-checked in Task 5.
   - "过期时自动续期一次；续不了再提示扫码": Task 4, with the `RenewalError` → "Run `mifit login`" path.
   - "不能无限重试 (≤3)": Task 4's rule and tests.
   - Design §3 credential handling: Global Constraints, redaction tests and the owner-only file reused through `save_session`, which writes atomically.
   - Roadmap §2 row: Task 5. ✓
2. **Step scan:** every code step names its file, signature and pinned values (URL, params, cookie names, event names, message copy, caps). The only code block is the 401 rule, the safety logic the tests alone would leave ambiguous. ✓
3. **Type consistency:** `Session.pass_token/device_id/renewed_at/can_renew()`, `new_device_id()`, `exchange_sts(..., device_id=)`, `_parse_login_content(response, what, *, known)`, `RenewalError(message, *, code)`, `renew_session(session, http=None, *, sid=SID)`, `record_event(event, session, *, now, path)`, `describe_session(session, now)`, `Transport(..., renew=, max_renewals=3)`, `Transport.session`, `MiFit(..., auto_renew=True)`, `MiFit.session`: the same names in every task. ✓
4. **Review Focus:** five lines, each pinned to named tests in Tasks 1–4. ✓
5. **Proportion:** 4 code tasks plus 1 manual task, about 47 new test functions, one short code block. ✓
