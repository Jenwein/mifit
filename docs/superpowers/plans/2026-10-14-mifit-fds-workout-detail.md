# mifit Workout Detail + GPS Track via FDS (M6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `workout_detail` domain. For every outdoor run (format 22) and outdoor ride (format 23) in a window, it fetches the workout's per-second record file and its GPS file from Xiaomi's FDS object store, decrypts and decodes them, and returns `WorkoutDetail(workout, samples, track)`. B holds only verified fields; `raw` holds every decoded field.

**Architecture:**
- **Xiaomi-specific code** goes into a new subpackage, `mifit.fds`:
  - `ids`: file names;
  - `urls`: the signed link request, sent through the existing `Transport` with the `healthapp/` base path;
  - `download`: a separate, cookie-free downloader;
  - `cipher`: AES decryption, the only module that uses `cryptography`;
  - `binary` and `layouts`: bounds-checked decoding with three hand-written tables taken from the APK's legacy parser.
- **`domains/workouts.py`** hands its row loop to a shared `sport_rows`. `workout_detail` then reads the workout list again to get the outer `sid`, and `workouts` does not change.
- **`domains/workout_detail.py`** works one workout at a time: 1 link request and at most 2 downloads, for at most the newest 10 supported workouts per call. It maps the decoded files to neutral `WorkoutSample` / `TrackPoint` records.
- **Exposure:**
  - the facade: `MiFit.workout_detail`;
  - the CLI: `mifit export workout_detail [--no-track]`;
  - T3's chunking: the `week` size, option forwarding in `read_chunks`, and the track setting stored in the `.partial` header.

**Tech Stack:** Python 3.12+, `requests`, new hard dependency `cryptography>=42`, stdlib `struct`/`hashlib`/`base64`, existing `Transport`, `DomainResult`, `REGISTRY`, `mifit.chunks`, `mifit.cli.partial`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-mifit-fds-design.md` . "§n" and "Dn" below point into it. Background:
- design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md` §2, §8.1, §12;
- `notes/10` (workout-list window rule) and `notes/11` §3 (signing-path rule);
- the T3 plan `docs/superpowers/plans/2026-10-14-mifit-long-range-export.md`, for the interfaces of `mifit.chunks`, `mifit.cli.partial` and `tests/support.py`.

**Precondition:**
- **T3 is merged into `main`.** That brings:
  - `mifit.chunks` (`DOMAIN_CHUNK`, `read_chunks`), `MiFit.read_chunks` and `PagedResult.stalled`;
  - `tests/support.py` with `PagedFake`, `exception_chain` and `synthetic_session`;
  - the CLI's `_json_ready`, `mifit.cli.partial` (`export_header`, `load_partial`, `PartialError`) and the chunked `_cmd_export`;
  - T3 Task 7's shared row decoder in `domains/base.py`.
- **Before Task 1, the controller commits** the spec as `docs/superpowers/specs/2026-10-14-mifit-fds-design.md` and this plan as `docs/superpowers/plans/2026-10-14-mifit-fds-workout-detail.md`, with the message `docs: M6 FDS design and plan`.
- **Branch:** work goes onto a short branch that is fast-forwarded into `main`.
- **Baseline:** write down the count that `python -m pytest -q` prints on that `main`.

## File map

| File | Task | Responsibility |
|---|---|---|
| `pyproject.toml` | 1 | `cryptography>=42` dependency |
| `src/mifit/fds/__init__.py` | 1, 3 | package docstring: chain, safety rules, "Xiaomi-specific seam", low-level use |
| `src/mifit/fds/ids.py` | 1 | `FileRef`, `key_bytes`, `suffix`, `server_key`, `RECORD`/`GPS` |
| `src/mifit/fds/cipher.py` | 1 | `decrypt_body` (the only `cryptography` import) |
| `src/mifit/fds/binary.py` | 2 | `FileHeader`, `parse_header`, `validity_len`, `Reader`, `FdsFormatError` |
| `src/mifit/fds/layouts.py` | 2 | field tables, `LAYOUTS`, `selected_fields`, `decode` → `DecodedFile` |
| `src/mifit/fds/urls.py` | 3 | `request_urls` → `{server_key: FdsEntry}` |
| `src/mifit/fds/download.py` | 3 | `FdsDownloader.fetch`, `FdsUnavailable` |
| `src/mifit/models/workout_detail.py` | 4 | `WorkoutSample`, `TrackPoint`, `WorkoutDetail` |
| `src/mifit/domains/workouts.py` | 4 | `SportRow`, `SportRows`, `sport_rows`; `_to_workout` → `to_workout` |
| `src/mifit/domains/workout_detail.py` | 4, 5 | `to_samples`, `to_track` (4); `fetch_workout_detail` (5) |
| `src/mifit/chunks.py` | 5, 6 | `DOMAIN_CHUNK["workout_detail"] = "week"` (5); `read_chunks(..., options=)` (6) |
| `src/mifit/client.py`, `src/mifit/__init__.py` | 6 | `MiFit.workout_detail`, `fds_http=`, `read_chunks(options=)`, exports |
| `src/mifit/cli/__init__.py`, `src/mifit/cli/partial.py` | 6 | `--no-track`, option forwarding, `track` in the partial header |
| `tests/fds_fixtures.py` | 1–5 | synthetic builders and fakes (test-only, not collected) |
| `notes/12-fds-workout-detail.md`, `README.md`, `docs/superpowers/roadmap.md` | 7 | close-out, "待真机核对" |

## Global Constraints

- **Read-only, low frequency.**
  - No task talks to the service, and tests stay offline (`tests/conftest.py`).
  - At run time, a call makes 1 list read (at most 20 pages, as in `workouts`), then 1 `gen_download_url` and at most 2 downloads per supported workout.
  - Every download is paced like `Transport`: `delay_s` (1.0) plus `random.uniform(0, 0.5 * delay_s)` before each GET. The delay is injectable, and `0` disables the pause.
- **Signing-path rule (§3, §4.3).**
  - The request is `t.get_json("service/gen_download_url", data, sign_path="/service/gen_download_url", base_path="healthapp/")`: the URL path is `/healthapp/service/gen_download_url`, and the signature covers `/service/gen_download_url`.
  - Business parameters go only into the signed `data`: `{"did": <sid>, "items": [{"timestamp": <value.time>, "suffix": <suffix>}, ...]}`, with record before GPS.
- **Dependency:**
  - `cryptography>=42` goes into `[project].dependencies` (a hard dependency, not an extra, D7). It is imported only in `src/mifit/fds/cipher.py`, at module top.
  - No other new dependency. `.github/workflows/ci.yml` is unchanged, because `pip install -e ".[test]"` installs it.
- **Never in records, `raw`, notes, exception messages or reprs:**
  - the presigned URL (its path or query);
  - `obj_key`, `obj_name` and `expires_time`;
  - `sid`/`did`, a suffix or a server key.

  A note may name an HTTP status, an exception *type*, a host name, a format or version number, or a workout's position.
- **Downloads (§11):** a downloader separate from `Transport`.
  - It sends no headers, cookies or auth of its own: exactly `http.get(url, timeout=30.0, allow_redirects=False, stream=True)`.
  - Only `https` URLs are fetched, and the host must end with `.fds.api.xiaomi.com` (a constructor parameter). A URL that carries a user name is refused.
  - Only HTTP 200 is accepted; 3xx counts as a failure.
  - The body is streamed with a **16 MiB** cap.
  - A `requests`/`OSError` failure becomes `FdsUnavailable`, raised outside the `except` block.
- **Untrusted bytes (§11):**
  - Reads are plain `struct`/`int.from_bytes`, and each one is bounds-checked first.
  - `recordCount × point size` is checked against the remaining bytes before the point loop, and a file holds at most **1,000,000** points.
  - The header must match the summary (`time`, `timezone`, `proto_type`). A version or mode outside the table gives the header only.
  - No recursion, no `eval`, no disk writes, and no cache (D16).
- **Time (D11):** every time is true Unix seconds (`resumeTimestamp + i`, the GPS `time`). The app's display shift (`getCurTzDiffInSec`) is never applied.
- **A + B (§8, §9):**
  - A uses the schema field names and stored, unscaled values. A float32 becomes a Python `float`, unrounded. Invalid sub-values and `exists=0` fields are absent.
  - B holds exactly the §8 fields, and scaling happens only in the B mapping.
  - No Mi field name appears in `models/`.
- **Anti-goal:** no GPX, TCX, FIT or any other file-format output. The CLI writes `{records, raw, notes}` only.
- **Scope (D1):** enabled layouts are record v8 for format 22, record v5 for format 23, and GPS v2 for both. Anything else gives its header plus a note.
- **Fixtures:**
  - Synthetic only, built in tests by `tests/fds_fixtures.py`, which packs bytes from the spec's tables without importing `mifit.fds.layouts`.
  - Never commit a file from `work/` or bytes derived from one. `work/` and `.superpowers/` are git-ignored.
- **No upstream code is ported (D17).**
  - `THIRD_PARTY_NOTICES` and `tests/test_licensing.py` stay unchanged.
  - If an implementer ports anything from `reference/mi-fitness-sync`, they must:
    - start that file with the T1 comment header (`adapted from Mi-Fitness-Sync` … `See THIRD_PARTY_NOTICES`), above the module docstring;
    - list it in `THIRD_PARTY_NOTICES` as `  - src/mifit/<path>.py` with a "from …" line;
    - add the path to `test_notices_list_the_adapted_files`.
- **Backward compatible:**
  - `workouts` output stays byte-identical, and `tests/test_domain_workouts.py` passes with its existing tests unchanged.
  - Existing CLI flags, `MiFit` methods and partial headers of other domains are unchanged.
  - The only existing tests that are edited are named in Tasks 1, 5 and 6.
- **Tests green after every task:** `python -m pytest -q`.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **A window with a strength session (format 8) beside a run and a ride.**
  - No FDS request goes out for the strength session, and it gets one note.
  - The run and the ride are read in full.
  - → Task 5 `test_unsupported_sport_sends_no_request`, `test_maps_a_run_and_a_ride`.
- **A ride whose report `time` is one second before its `start_time` (seen live).**
  - File names and the header check use `time`, and samples start at `time`.
  - Selection still uses the outer `time`.
  - → Task 5 `test_files_are_named_by_the_report_time_not_the_start`.
- **A credential that reaches the user.** The presigned URL, `obj_key`, `obj_name`, `sid`, a suffix or `expires_time` must never appear in `raw`, `records`, notes, a repr, or the text of an exception, including a `requests` error that quotes the URL.
  - → Task 3 `test_network_error_message_has_no_url_and_no_chain`, `test_entry_repr_hides_url_and_key`.
  - → Task 5 `test_output_never_contains_link_secrets`.
- **A hostile or damaged file.** Examples: a `recordCount` beyond the bytes, a file cut inside a point, zero-size points with a huge count, a header from another workout.
  - Each gives `None` plus a note: no half data, no hang, no crash.
  - → Task 2 `test_record_count_beyond_remaining_bytes_is_refused`, `test_total_points_are_capped`.
  - → Task 5 `test_damaged_file_gives_none_and_note`, `test_header_mismatch_is_not_parsed`.
- **`--resume` of a `--no-track` export run without `--no-track`, or the reverse.**
  - It is refused and never merges weeks with and without tracks.
  - → Task 6 `test_resume_with_a_different_track_setting_is_refused`.

---

### Task 1: `cryptography`, file names (`fds.ids`) and body decryption (`fds.cipher`)

**Files:**
- Modify: `pyproject.toml`, `tests/test_cli.py` (`test_pyproject_declares_package_dependencies_and_entry_point` only)
- Create: `src/mifit/fds/__init__.py`, `src/mifit/fds/ids.py`, `src/mifit/fds/cipher.py`, `tests/fds_fixtures.py`, `tests/test_fds_ids.py`, `tests/test_fds_cipher.py`

**Interfaces:**
- Produces, in `fds/ids.py` (§4.2):

```python
RECORD = 0
GPS = 2

@dataclass(frozen=True)
class FileRef:
    sid: str = field(repr=False)  # the outer row's sid; hashed into the name, never output
    timestamp: int                # value.time in seconds (NOT start_time)
    tz15: int                     # value.timezone, quarter hours
    proto: int                    # value.proto_type
    file_type: int                # RECORD or GPS

def key_bytes(ref: FileRef) -> bytes   # struct.pack("<Ib", timestamp, tz15) + bytes([((1 << 7) + (proto << 2) + file_type) & 0xFF])
def suffix(ref: FileRef) -> str        # b64url_nopad(key_bytes) + "_" + b64url_nopad(sha1(sid.encode("utf-8")))
def server_key(ref: FileRef) -> str    # f"{suffix(ref)}_{timestamp}"
```

  - `FileRef.__post_init__` raises `ValueError` unless all of these hold:
    - `sid` is a non-empty `str`;
    - `0 <= timestamp < 2**32` (the APK's millisecond branch is out of scope);
    - `-128 <= tz15 <= 127`;
    - `0 <= proto <= 31`;
    - `file_type in (RECORD, GPS)`.

    Its message is fixed and never quotes `sid`.
- Produces, in `fds/cipher.py` (§4.5, D22): `IV = b"1234567887654321"`, `class FdsDecryptError(ValueError)`, and `def decrypt_body(body: bytes, key_b64: str) -> bytes`.
  - **Body text:** the body must be ASCII; surrounding whitespace is stripped.
  - **Two forms:** a body starting with `"` must `json.loads` to a `str`. Any other body is taken as bare text.
  - **Base64url:** both the text and the key go through one strict decoder:
    - the alphabet is `[A-Za-z0-9_-]`, with at most two trailing `=`;
    - nothing else is allowed, including no inner whitespace;
    - padding is added before decoding, and `len % 4 == 1` is refused.
  - **Ciphertext:** non-empty and a multiple of 16 bytes.
  - **Key:** 16, 24 or 32 bytes after decoding.
  - **Decryption:** AES-CBC with the fixed `IV`, then PKCS#7 removal (`padding.PKCS7(128)`).
  - **Failures:** every one raises `FdsDecryptError` with a fixed message, raised after the `except` block. The message never quotes the body or the key.
- Produces, in `tests/fds_fixtures.py` (test-only; it imports `cryptography` directly, never `mifit.fds.cipher`):
  - `SYNTH_SID = "1000000006"`, `SYNTH_KEY = bytes(range(16))`, `SYNTH_KEY_B64 = b64url(SYNTH_KEY)`;
  - `b64url(raw: bytes) -> str`, which returns base64url without padding;
  - `encrypt_plain(plain: bytes, key: bytes = SYNTH_KEY, *, iv: bytes = b"1234567887654321", pad: bool = True) -> bytes`, which returns the raw ciphertext;
  - `encrypt_body(plain: bytes, key: bytes = SYNTH_KEY, *, quoted: bool = True) -> bytes`, which returns the download body: `b'"' + b64url + b'"'`, or the bare text when `quoted` is false.

- [ ] **Step 1: Write the failing tests.** All expected values below were computed with Python from the §4.2 rule.
  - **`test_fds_ids.py`:**
    - `test_key_bytes_vector`: `key_bytes(FileRef("1234567890", 1700000000, 32, 22, RECORD)) == bytes.fromhex("00f1536520d8")`.
    - `test_suffix_vectors`, parametrized over `(sid, timestamp, tz15, proto, file_type) -> suffix`:
      - `("1234567890", 1700000000, 32, 22, RECORD)` → `"APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo"`;
      - `("1234567890", 1700000000, 32, 22, GPS)` → `"APFTZSDa_AbMHrLpPVPVar8M7sGu79sqAPpo"`;
      - `("1234567890", 1700000000, -20, 23, RECORD)` → `"APFTZezc_AbMHrLpPVPVar8M7sGu79sqAPpo"`;
      - `("1234567890", 1700000000, -20, 23, GPS)` → `"APFTZeze_AbMHrLpPVPVar8M7sGu79sqAPpo"`;
      - `("1000000006", 1700000003, 32, 22, RECORD)` → `"A_FTZSDY_cp-gLXCjBkAd5lnqRh1s3r_Zyd4"`. This row checks the URL-safe alphabet and the missing padding.
    - `test_server_key_appends_the_timestamp`: `server_key(FileRef("1234567890", 1700000000, 32, 22, RECORD)) == "APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo_1700000000"`.
    - `test_out_of_range_refs_are_refused`, parametrized:
      - timestamps `2**32`, `1700000000000` and `-1`;
      - tz15 `128` and `-129`;
      - proto `32` and `-1`;
      - file_type `1` and `3`;
      - sid `""` and `None`.

      Each raises `ValueError`, and `"1234567890"` is not in `str(exc)`.
    - `test_repr_hides_the_source_id`: `"1234567890" not in repr(FileRef("1234567890", 1700000000, 32, 22, RECORD))`.
  - **`test_fds_cipher.py`:**
    - `test_round_trip`, parametrized over plaintext lengths 0, 15, 16, 17 and 1000 and over quoted/bare bodies: `decrypt_body(encrypt_body(p, quoted=q), SYNTH_KEY_B64) == p`.
    - `test_aes_256_key`: a 32-byte key round-trips.
    - `test_key_with_padding_is_accepted`: `SYNTH_KEY_B64 + "=="` works.
    - `test_iv_is_the_fixed_one`: a 48-byte plaintext encrypted with `iv=b"0" * 16` decrypts to bytes whose `[16:]` equals the plaintext's `[16:]` and whose `[:16]` does not.
    - `test_rejections`, parametrized with ids. Each case raises `FdsDecryptError`:
      - `key-15-bytes`;
      - `key-bad-char` (a `+` in the key);
      - `body-json-number` (`b"123"`);
      - `body-json-object`;
      - `body-not-ascii` (`b"\xff"`);
      - `body-bad-char` (a quoted text containing `+`);
      - `body-inner-space`;
      - `ciphertext-not-block-multiple` (base64url of 20 bytes);
      - `empty-ciphertext` (`b'""'`);
      - `bad-padding` (the quoted base64url of `encrypt_plain(b"\x00" * 16, pad=False)`, which decrypts to a last byte of `0x00`).
    - `test_errors_quote_nothing_and_have_no_chain`: a body of `b'"SYNTH-BODY-MARK'` (an unterminated JSON string) and a key of `"SYNTH-KEY-MARK+"`.
      - The marker is in neither `str(exc)` nor `repr(exc)`.
      - `support.exception_chain(exc) == [exc]`.
    - `test_cryptography_is_imported_only_by_the_cipher`: among `src/mifit/**/*.py`, only `src/mifit/fds/cipher.py` has a line matching `^\s*(from|import)\s+cryptography`.
  - **`test_cli.py`:** in the pyproject test, the expected `names` become `{"requests", "qrcode", "cryptography"}`, and `"cryptography>=42" in project["dependencies"]`.
- [ ] **Step 2: Run** `python -m pytest tests/test_fds_ids.py tests/test_fds_cipher.py tests/test_cli.py -q`. Expected: FAIL, because the modules and the dependency are missing.
- [ ] **Step 3: Implement.**
  - Add `"cryptography>=42"` to `dependencies`, then run `python -m pip install -e ".[test]"`.
  - Write `fds/__init__.py`'s docstring:
    - the chain of §4.1 in five lines;
    - the safety rules (Global Constraints: never-output list, download hygiene);
    - that the package is the Xiaomi-specific seam (design §12.3) and that neutral models live in `models/`.
  - Write `ids.py` and `cipher.py`.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add pyproject.toml src/mifit/fds tests/fds_fixtures.py tests/test_fds_ids.py tests/test_fds_cipher.py tests/test_cli.py && git commit -m "feat(fds): FDS file names and body decryption (cryptography dependency)"`

---

### Task 2: Cloud file header and the three layouts (`fds.binary`, `fds.layouts`)

**Files:**
- Create: `src/mifit/fds/binary.py`, `src/mifit/fds/layouts.py`, `tests/test_fds_layouts.py`
- Modify: `tests/fds_fixtures.py` (file builders)

**Interfaces:**
- Produces, in `fds/binary.py` (§4.6):
  - `HEADER_LEN = 8` and `class FdsFormatError(ValueError)`. Its messages are fixed fragments, such as `"the file ends inside its header"`, `"a segment claims more points than the file holds"`, `"the file holds more than 1000000 points"` and `"the validity flags are shorter than the field table"`. They never hold file bytes.
  - `@dataclass(frozen=True) class FileHeader(timestamp: int, timezone: int, version: int, proto_type: int, mode: int, validity: bytes | None)`.
    - Its `as_raw(self) -> dict` returns `{"timestamp", "timezone", "version", "protoType", "encryptionCompressionMode", "validityFlags": validity.hex()}`.
    - `validityFlags` is left out when `validity is None`.
  - `def validity_len(proto: int, file_type: int, version: int) -> int | None` encodes the `FitnessDataValidity` table:
    - `(22, RECORD)`: v1 → 2, v2–4 → 3, v5 → 5, v6–7 → 6, v8–9 → 7;
    - `(23, RECORD)`: v1–2 → 2, v3–6 → 3, v7 → 4;
    - `(any proto, GPS)`: v1–4 → 1, v5 → 2;
    - anything else → `None`.
  - `def parse_header(plain: bytes, file_type: int) -> FileHeader` reads `struct.unpack_from("<IbBBB", plain)` (timestamp, timezone, version, proto, mode).
    - It then reads `validity_len(proto, file_type, version)` bytes of flags, or sets `None` when the table has no entry.
    - Too few bytes for either part raise `FdsFormatError`.
  - `class Reader(data: bytes, offset: int = 0)` has `remaining: int`, `offset: int`, `uint(size: int) -> int` (little-endian), `f32() -> float` and `skip(size: int)`. Each read checks bounds first and raises `FdsFormatError`.
- Produces, in `fds/layouts.py` (§4.7, §4.8):
  - `MAX_POINTS = 1_000_000`.
  - `@dataclass(frozen=True) class Field4D(names: tuple[str, ...], size: int, bits: tuple[tuple[int, int] | None, ...], since: int, until: int | None = None)`.
    - `names` and `bits` run high → middle → low.
    - A `bits` entry is `(start, length)`, or `None` for the whole value.
  - `RUN_FIELDS` (format 22), as `names / size / bits / since–until`, in byte order:
    1. `newCalories, newSteps` / 1 / (4,4), (0,4) / 1–
    2. `heartRate` / 1 / whole / 1–
    3. `isKilometer, isAltitudeRising, altitudeChange` / 1 / (7,1), (6,1), (0,6) / 1–8
    4. `newDistance` / 1 / whole / 1–8
    5. `stride` / 1 / whole / 2–
    6. `newImpactForce, newContactTime, newFlightTime` / 4 / (26,6), (13,13), (0,13) / 4–
    7. `contactRatio` / 1 / whole / 5–
    8. `cadence` / 1 / whole / 5–
    9. `pace` / 2 / whole / 5–
    10. `equalIntensityPace` / 2 / whole / 6–
    11. `runningPower` / 2 / whole / 6–
    12. `verticalStrideRatio` / 2 / whole / 8–
    13. `verticalAmplitude` / 2 / whole / 8–
  - `RIDE_FIELDS` (format 23), in byte order:
    1. `newCalories, workoutcount` / 1 / (4,4), (0,4) / 1–
    2. `heartRate` / 1 / whole / 1–
    3. the altitude triple as in run / 1 / … / 1–5
    4. `newDistance` / 1 / whole / 1–5
    5. `speed` / 2 / whole / 3–
    6. `cadence` / 1 / whole / 3–
  - `RUN_SEGMENT` = `(("initialAltitude", "<f"), ("recordCount", "<I"), ("resumeTimestamp", "<I"), ("segmentInfo", "<B"), ("totalDistance", "<I"), ("totalDuration", "<I"), ("totalSteps", "<I"), ("segmentPace", "<H"))`, 27 bytes.
  - `RIDE_SEGMENT` = the same first four, then `("totalDistance", "<I"), ("totalTime", "<I"), ("totalSpeed", "<H")`, 23 bytes.
  - `@dataclass(frozen=True) class GpsField(name: str, size: int, code: str | None, since: int)`.
  - `GPS_FIELDS` = `time 4 "<I" 1`, `longitude 4 "<f" 1`, `latitude 4 "<f" 1`, `locationHorizontalAccuracy 4 "<f" 2`, `locationSpeedAndGpsSource 2 "<H" 2`, `gpsSourceValid 0 None 2`.
  - `RecordLayout(fields: tuple[Field4D, ...], segment: tuple[tuple[str, str], ...])` and `GpsLayout(fields: tuple[GpsField, ...])`, both frozen dataclasses.
  - `LAYOUTS: dict[tuple[int, int, int], RecordLayout | GpsLayout]` is keyed `(proto, file_type, version)`. It has exactly `(22, RECORD, 8)`, `(23, RECORD, 5)`, `(22, GPS, 2)` and `(23, GPS, 2)`.
  - `def selected_fields(fields: Sequence[Field4D], version: int) -> list[Field4D]` keeps the fields with `since <= version` and `until is None or version <= until`, in table order.
  - `@dataclass(frozen=True) class DecodedFile(raw: dict, trailing_bytes: int)`.
  - `def decode(plain: bytes, header: FileHeader, layout: RecordLayout | GpsLayout) -> DecodedFile`. `raw` is `{"header": header.as_raw(), "segments": [...]}` for a record and `{"header": ..., "gpsPoints": [...]}` for GPS.
- **Record decode** (`FourDimenDataParser`; a rule the tests pin):
  - **Nibbles.** The k-th selected field uses nibble `validity[k // 2] >> 4` when k is even and `validity[k // 2] & 0x0F` when k is odd. Bit `8` is `exists`. The field's sub-values take bits `4`, `2` and `1`, in order.
  - **Flag length.** `len(fields) > 2 * len(validity)` raises `FdsFormatError`.
  - **Point size.** It is the sum of `size` over the fields with `exists`. A field without `exists` takes no bytes.
  - **Segment loop.** While `remaining >= segment size`:
    - read the segment header in order;
    - replace `segmentInfo` with `pauseType = info >> 7` and `segmentType = info & 0x7F`, keeping the §9 key order;
    - add `recordCount` to a running total, and raise `FdsFormatError` if the total exceeds `MAX_POINTS`;
    - raise `FdsFormatError` if `recordCount * point_size > remaining`;
    - read the points into `dataPoints`.
  - **Points.** Each point reads every existing field as a little-endian uint of `size` bytes. A sub-value goes into the point only when its valid bit is set: the whole value when `bits` is `None`, else `(value >> start) & ((1 << length) - 1)`.
  - **Trailing bytes.** Whatever is left, fewer bytes than one segment header, is `trailing_bytes`.
- **GPS decode** (`OneDimenDataParser` / `SportGpsParser`):
  - **Fields.** The selected fields are those with `since <= version`. Field k is valid when bit `(8 * len(validity) - 1 - k)` of `int.from_bytes(validity, "big")` is set.
  - **Size.** Every field takes its bytes whether or not it is valid. The point size is 18 for v2.
  - **Count.** `count = remaining // point_size` (more than `MAX_POINTS` raises), and `trailing_bytes = remaining % point_size`.
  - **Speed and source.** `locationSpeedAndGpsSource`, when valid, becomes `locationSpeed = (v & 0xFFF0) >> 4` (stored value, not divided by 10). It also gives `gpsSource = v & 0x0F`, but only when `gpsSourceValid` is valid.
  - **Others.** Every other valid field keeps its own name and value.
- Produces, in `tests/fds_fixtures.py` (packed by hand from the spec tables; does not import `mifit.fds.layouts`):
  - `header_bytes(ts: int, tz15: int, version: int, proto: int, mode: int = 0) -> bytes`.
  - `run_point(**fields) -> bytes` gives 21 bytes, every v8 field present.
    - Keyword defaults: `calories=1, steps=3, heart_rate=120, kilometer=0, rising=1, altitude_change=5, distance_dm=25, stride=80, impact=10, contact=250, flight=100, contact_ratio=40, cadence=170, pace=360, equal_pace=365, power=200, vertical_ratio=85, vertical_amplitude=80`.
  - `ride_point(*, with_cadence: bool = True, **fields) -> bytes` gives 7 bytes, or 6 without cadence.
    - Keyword defaults: `calories=2, workoutcount=0, heart_rate=110, kilometer=0, rising=0, altitude_change=3, distance_m=5, speed=250, cadence=0`.
  - `run_segment(points, *, resume, initial_altitude=50.0, pause_type=1, segment_type=4, total_distance=0, total_duration=None, total_steps=0, segment_pace=0, record_count=None) -> bytes`.
  - `ride_segment(points, *, resume, initial_altitude=50.0, pause_type=1, segment_type=0, total_distance=0, total_time=None, total_speed=0, record_count=None) -> bytes`.
    - In both, `record_count` defaults to `len(points)`, and `total_duration` / `total_time` default to it.
  - `gps_point(time: int, lon: float, lat: float, *, accuracy: float = 4.5, speed: int = 30, source: int = 0) -> bytes` gives 18 bytes, longitude first.
  - `record_file(ts, tz15, proto, version, flags, *segments) -> bytes` and `gps_file(ts, tz15, proto, *points, flags=b"\xff", version=2) -> bytes`.
  - Flag constants:
    - `RUN_FLAGS_ALL = bytes.fromhex("ffffffffffffff")`;
    - `RUN_FLAGS_LIVE = bytes.fromhex("fffffabffbbfff")`;
    - `RIDE_FLAGS_ALL = bytes.fromhex("ffffff")`;
    - `RIDE_FLAGS_LIVE = bytes.fromhex("dffffb")`;
    - `RIDE_FLAGS_NO_CADENCE = bytes.fromhex("dffff3")`.
  - `f32(x: float) -> float` is `struct.unpack("<f", struct.pack("<f", x))[0]`.

- [ ] **Step 1: Write the failing tests in `test_fds_layouts.py`:**
  - **Header:**
    - `test_parse_header_reads_the_cloud_header`: `record_file(1700000000, -20, 22, 8, RUN_FLAGS_LIVE)` gives `FileHeader(1700000000, -20, 8, 22, 0, RUN_FLAGS_LIVE)`. Its `as_raw()["validityFlags"] == "fffffabffbbfff"`.
    - `test_validity_lengths`:
      - `(22, RECORD)`: v1 → 2, v4 → 3, v5 → 5, v7 → 6, v8 → 7, v9 → 7, v10 → None;
      - `(23, RECORD)`: v2 → 2, v6 → 3, v7 → 4, v8 → None;
      - `(23, GPS)`: v4 → 1, v5 → 2, v6 → None;
      - `(8, RECORD, 1)` → None.
    - `test_short_files_are_refused`: 7 bytes, and 8 bytes of a v8 run header without its flags, both raise `FdsFormatError`.
    - `test_unknown_validity_gives_no_flags`: a format-8 record header has `validity is None`, and `"validityFlags" not in as_raw()`.
  - **Tables:**
    - `test_run_v8_fields_in_byte_order`: `[f.names for f in selected_fields(RUN_FIELDS, 8)]` lists the 13 rows above, in order.
    - `test_ride_v5_fields_in_byte_order`: the 6 rows above.
    - `test_enabled_layouts`: `set(LAYOUTS) == {(22, 0, 8), (23, 0, 5), (22, 2, 2), (23, 2, 2)}`.
  - **Record (format 22, v8):**
    - `test_run_v8_all_valid_point`: one segment of one `run_point()` under `RUN_FLAGS_ALL` decodes to the 18 sub-values of the defaults, keyed by the A names (for example `newDistance: 25`, `newContactTime: 250`), with `trailing_bytes == 0`.
    - `test_run_bit_fields_at_their_limits`: `run_point(calories=15, steps=15, kilometer=1, altitude_change=63, impact=63, contact=8191, flight=8191)` decodes to those exact values.
    - `test_run_v8_live_shape_flags` (§4.7 test vector):
      - under `RUN_FLAGS_LIVE`, two points decode;
      - every point lacks `newImpactForce`, `newFlightTime`, `contactRatio`, `equalIntensityPace` and `runningPower`, and keeps `newContactTime`;
      - the file length is exactly `8 + 7 + 27 + 2 * 21`.
  - **Record (format 23, v5):**
    - `test_ride_v5_live_shape_flags`: under `RIDE_FLAGS_LIVE`, 7-byte points decode, and no point has `workoutcount` or `cadence`.
    - `test_field_without_exists_takes_no_bytes`: under `RIDE_FLAGS_NO_CADENCE`, 6-byte points (`ride_point(with_cadence=False)`) decode to the same values as under `RIDE_FLAGS_LIVE`.
  - **Segments:**
    - `test_segments_and_pause_info`:
      - two segments, the second with `resume = first resume + 100`;
      - `segmentInfo` `0x84` becomes `pauseType 1, segmentType 4`;
      - the segment keys are exactly `initialAltitude, recordCount, resumeTimestamp, pauseType, segmentType, totalDistance, totalDuration, totalSteps, segmentPace, dataPoints`, in that order;
      - `initialAltitude` is a `float`.
    - `test_record_count_beyond_remaining_bytes_is_refused`: `record_count=3` with 2 points raises `FdsFormatError`.
    - `test_file_cut_inside_a_point_is_refused`: the last 5 bytes are removed, and decoding raises `FdsFormatError`.
    - `test_total_points_are_capped`: validity `bytes.fromhex("77777777777777")` (no field exists, so the point size is 0) with `record_count=2**32 - 1` raises `FdsFormatError` in well under a second.
    - `test_short_tail_is_trailing`: 5 extra bytes after the last segment give `trailing_bytes == 5` with every segment intact.
  - **GPS:**
    - `test_gps_v2_points`: three `gps_point(t, 20.0001 + i / 1e4, 10.0001 + i / 1e4, speed=30, source=2)`.
      - `gpsPoints[i] == {"time": t, "longitude": f32(...), "latitude": f32(...), "locationHorizontalAccuracy": f32(4.5), "locationSpeed": 30, "gpsSource": 2}`.
      - Longitude ≠ latitude, which would catch a swapped order.
    - `test_gps_invalid_field_still_takes_bytes`: flags `0xEC` (accuracy invalid) leave out `locationHorizontalAccuracy`, and `locationSpeed`/`gpsSource` are still right.
    - `test_gps_source_needs_its_valid_bit`: flags `0xF8` keep `locationSpeed` and drop `gpsSource`.
    - `test_gps_tail_is_trailing`: two points plus 5 bytes give 2 points and `trailing_bytes == 5`.
  - **Reader:** `test_reader_refuses_reads_past_the_end`.
- [ ] **Step 2: Run** `python -m pytest tests/test_fds_layouts.py -q`. Expected: FAIL, because the modules are missing.
- [ ] **Step 3: Implement** `binary.py` and `layouts.py`.
  - `layouts.py`'s docstring says:
    - where each table comes from (`OutdoorStepRecordParser`, `OutdoorNoStepRecordParser`, `SportGpsParser`);
    - that newer versions (record v9 / v6+, GPS v3+) need the APK's extra rows (spec §4.7–§4.8) before an entry is added to `LAYOUTS`;
    - that the names are the `schemas.zip` names (D6).
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/fds/binary.py src/mifit/fds/layouts.py tests/fds_fixtures.py tests/test_fds_layouts.py && git commit -m "feat(fds): cloud file header and record v8/v5, GPS v2 layouts"`

---

### Task 3: Download links (`fds.urls`) and the downloader (`fds.download`)

**Files:**
- Create: `src/mifit/fds/urls.py`, `src/mifit/fds/download.py`, `tests/test_fds_urls.py`, `tests/test_fds_download.py`
- Modify: `tests/fds_fixtures.py` (fakes), `src/mifit/fds/__init__.py` (one docstring paragraph)

**Interfaces:**
- Consumes: `Transport.get_json(endpoint_path, data_obj, *, sign_path, base_path)`; `FileRef`, `suffix`, `server_key` (Task 1); `support.PagedFake`, `support.exception_chain`.
- Produces, in `fds/urls.py` (§4.3):
  - `ENDPOINT = "service/gen_download_url"`, `SIGN_PATH = "/service/gen_download_url"`, `BASE_PATH = "healthapp/"`.
  - `@dataclass(frozen=True, repr=False) class FdsEntry(url: str, key_b64: str | None, method: str | None, expires_s: int | None)`.
    - `__repr__` returns `f"FdsEntry(host={host!r}, method={method!r}, has_key={key_b64 is not None})"`, where `host` is `urlsplit(url).hostname`.
    - `obj_name` is dropped.
  - `def request_urls(t: Transport, sid: str, refs: Sequence[FileRef]) -> dict[str, FdsEntry]`.
    - It makes one `get_json` with `{"did": sid, "items": [{"timestamp": r.timestamp, "suffix": suffix(r)} for r in refs]}` and the three constants.
    - A `result` that is not a `dict` raises `ApiError("The download-link answer carried no file map.")`, with no `payload` attached.
    - Each `result[key]` that is a dict with a `str` `url` becomes an entry:
      - `key_b64`: `obj_key` when it is a non-empty `str`, else `None` (the file does not exist, §4.3);
      - `method`: a `str` or `None`;
      - `expires_s`: `as_int(expires_time)`.
    - Other entries are left out. `ApiError`, `AuthError` and `OSError` from `get_json` propagate.
- Produces, in `fds/download.py` (§4.4, §11, D8):
  - `MAX_BODY_BYTES = 16 * 1024 * 1024`, `HOST_SUFFIXES = (".fds.api.xiaomi.com",)`.
  - `class FdsUnavailable(MiFitError)`. Its message is a fixed fragment that a note can embed: `"HTTP 404"`, `"redirect (HTTP 302) not followed"`, `"larger than 16 MiB"`, `"network error (ConnectTimeout)"`, `"not an https link"`, `"link carries a user name"`, `"unexpected host <hostname>"`.
  - `class FdsDownloader`:
    - **Constructor:** `__init__(self, http: Any = None, *, delay_s: float = 1.0, timeout_s: float = 30.0, max_bytes: int = MAX_BODY_BYTES, host_suffixes: Sequence[str] = HOST_SUFFIXES)`. With `http=None` it uses the `requests` **module**, imported lazily as in `Transport`, so each download runs in a fresh session and no cookie jar can carry anything from one download to the next.
    - **`fetch(self, url: str) -> bytes`** works in this order:
      1. Check the URL with `urlsplit`. The scheme must be `https`, the URL must have no user name or password, and its lowercased `hostname` must end with one of `host_suffixes`. On a failure, raise `FdsUnavailable` with no request and no pause.
      2. Pause: `time.sleep(delay_s + random.uniform(0, 0.5 * delay_s))` when `delay_s > 0`. Use module-level `import time, random`, so tests can patch `download_module.time.sleep` and `download_module.random.uniform`.
      3. Call `http.get(url, timeout=timeout_s, allow_redirects=False, stream=True)`, with exactly these keyword arguments.
      4. Check the status: 3xx gives a redirect failure, and any other status besides 200 gives `HTTP n`.
      5. Read `response.iter_content(65536)` and abort with `"larger than 16 MiB"` as soon as the total exceeds `max_bytes`.
      6. Close the response in a `finally`.
    - **Errors:** any `OSError` (every `requests` error is one) becomes `FdsUnavailable(f"network error ({type(exc).__name__})")`, raised after the `except` block.
- Produces, in `tests/fds_fixtures.py`:
  - `EXPIRES = 1711111111` and `URL_MARK = "SYNTH-SIGNATURE"`.
  - **`class FakeFdsService`** plays the link answer and the object store.
    - **Constructor:** `__init__(self, files: dict[str, bytes] | None = None, *, key: bytes = SYNTH_KEY, entry_overrides: dict[str, dict] | None = None, fetch_errors: dict[str, Exception] | None = None)`. `files` maps a server key to a **plaintext** file.
    - **`answer(data_obj) -> dict`** builds a result map with one entry per requested item, keyed `f"{suffix}_{timestamp}"`:
      - the entry's `url` is `f"https://cnbj1.fds.api.xiaomi.com/synthetic-bucket/{n}?GalaxyAccessKeyId=SYNTH-ACCESS&Expires={EXPIRES}&Signature={URL_MARK}-{n}"`;
      - it also has `"obj_name": f"SYNTH-OBJ-NAME-{n}"`, `"method": "GET"` and `"expires_time": EXPIRES`;
      - `obj_key` is `b64url(key)` only when the key is in `files`, as live;
      - `entry_overrides[key]` is merged in last.
    - **`fetch(url) -> bytes`** finds the server key whose `answer` URL this is, appends `url` to `self.fetched`, and returns `encrypt_body(files[server_key], self.key)`. When `fetch_errors` has that server key, it raises that exception instead. It has the `FdsDownloader` interface.
  - **`class FdsTransport(PagedFake)`:**
    - **Constructor:** `__init__(self, rows: list[dict], service: FakeFdsService, *, link_errors: dict[int, Exception] | None = None)`. It serves `rows` as `sport_records`.
    - **`get_json(self, endpoint_path, data_obj=None, *, sign_path=None, base_path=None)`:**
      - appends `{"endpoint_path", "data_obj" (deepcopy), "sign_path", "base_path"}` to `self.link_calls`;
      - raises `link_errors.get(<0-based call index>)` when that is set;
      - otherwise returns `{"code": 0, "message": "ok", "result": service.answer(data_obj)}`.
  - **`class StoreHttp`:** a fake `http` that records `(url, kwargs)` in `self.calls` and appends `("get", url)` to an optional `events` list.
    - `__init__(self, *, status=200, chunks=(b"body",), error=None, iter_error=None, events=None)`.
    - Its response has `status_code`, `headers`, `iter_content(size)` (which can raise `iter_error`) and `close()` (which sets `closed`).

- [ ] **Step 1: Write the failing tests.**
  - **`test_fds_urls.py`**, with `REFS` = the record and GPS `FileRef` of one synthetic run:
    - `test_request_shape_follows_the_signing_rule`: `FdsTransport.link_calls == [{"endpoint_path": "service/gen_download_url", "data_obj": {"did": SYNTH_SID, "items": [{"timestamp": T, "suffix": suffix(REFS[0])}, {"timestamp": T, "suffix": suffix(REFS[1])}]}, "sign_path": "/service/gen_download_url", "base_path": "healthapp/"}]`.
    - `test_entries_are_keyed_by_server_key`: with a record file only, the record entry has `key_b64 == SYNTH_KEY_B64` and `method == "GET"`. The GPS entry exists with `key_b64 is None`.
    - `test_malformed_entries_are_left_out`: overrides with `url` absent or not a string, and an entry that is a list, are not in the map.
    - `test_answer_without_a_file_map_raises_api_error`: a `result` of `[]` raises `ApiError` with `exc.payload is None`.
    - `test_entry_repr_hides_url_and_key`: neither `URL_MARK` nor `SYNTH_KEY_B64` is in `repr(entry)`, and `"cnbj1.fds.api.xiaomi.com"` is.
  - **`test_fds_download.py`** (`URL` = a FakeFdsService-style link containing `URL_MARK`; `delay_s=0` unless stated):
    - `test_fetch_sends_nothing_but_the_url`: `StoreHttp.calls == [(URL, {"timeout": 30.0, "allow_redirects": False, "stream": True})]`, and the chunks come back joined.
    - `test_default_http_is_the_requests_module`: `FdsDownloader()._http is requests`.
    - `test_redirect_is_refused`: a 302 raises `FdsUnavailable` containing `"302"` after exactly one GET, and the response is closed.
    - `test_non_200_is_refused`: a 404 gives `"HTTP 404"`.
    - `test_body_over_the_cap_is_aborted`: `max_bytes=10` with chunks `[b"x" * 8, b"x" * 8, b"x" * 8]` raises after the second chunk, and the response is closed.
    - `test_only_listed_https_hosts_are_fetched`, parametrized. Each URL raises `FdsUnavailable` and `StoreHttp.calls == []`:
      - `http://cnbj1.fds.api.xiaomi.com/a`;
      - `https://cnbj1.fds.api.xiaomi.com.evil.example/a`;
      - `https://evil.example/?h=.fds.api.xiaomi.com`;
      - `https://user:pw@cnbj1.fds.api.xiaomi.com/a`.
    - `test_host_suffixes_can_be_extended`: `host_suffixes=(".fds.api.xiaomi.com", ".synthetic-store.example")` accepts `https://x.synthetic-store.example/a`.
    - `test_network_error_message_has_no_url_and_no_chain`, parametrized over a `requests.ConnectionError(f"failed {URL}")` from `get` and a `requests.exceptions.ChunkedEncodingError(f"broke {URL}")` from `iter_content`:
      - the result is `FdsUnavailable`;
      - `URL_MARK` is in neither `str(exc)` nor `repr(exc)`;
      - `exception_chain(exc) == [exc]`;
      - the message names the type.
    - `test_each_download_is_paced`: with `delay_s=1.0`, patched `download_module.time.sleep` appending `("sleep", s)` to the same `events` list, and `random.uniform` returning `0.0`, two fetches give `events == [("sleep", 1.0), ("get", URL)] * 2`.
    - `test_jitter_matches_the_transport`: with `random.uniform` patched to `lambda a, b: b`, the sleep is `1.5`.
    - `test_refused_url_is_not_paced`: a bad host gives no sleep.
- [ ] **Step 2: Run** `python -m pytest tests/test_fds_urls.py tests/test_fds_download.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `urls.py` and `download.py`. Add a "Low-level use" paragraph to `fds/__init__.py` (§9): `request_urls(t, sid, refs)` gives a map of entries; `FdsDownloader().fetch(entry.url)` gives the encoded body; `decrypt_body(body, entry.key_b64)` gives the plaintext file; nothing is cached or written.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/fds tests/fds_fixtures.py tests/test_fds_urls.py tests/test_fds_download.py && git commit -m "feat(fds): download links and a cookie-free, size-capped downloader"`

---

### Task 4: Neutral models, sample/track mapping, shared sport-row read

**Files:**
- Create: `src/mifit/models/workout_detail.py`, `src/mifit/domains/workout_detail.py` (mapping functions only; no `@register` yet), `tests/test_workout_detail_mapping.py`
- Modify: `src/mifit/domains/workouts.py`; `tests/test_domain_workouts.py` (new tests appended; existing ones untouched)

**Interfaces:**
- Produces, in `models/workout_detail.py`:
  - `WorkoutSample` and `TrackPoint` exactly as spec §8, with their docstrings verbatim.
  - `@dataclass class WorkoutDetail(workout: Workout, samples: list[WorkoutSample] | None, track: list[TrackPoint] | None)`. Its docstring says:
    - `workout` is the same summary `workouts` gives;
    - `samples`/`track` are `None` when not read (the result's notes say why) and `[]` when the file was read but held no points;
    - the SDK writes no GPX, TCX or FIT; these records are the building blocks for that.
- Produces, in `domains/workouts.py` (behavior and output unchanged):

```python
@dataclass(frozen=True)
class SportRow:
    value: dict | None                    # decoded value (the object raw gets), or None when skipped
    note: str | None = None               # why it was skipped; the exact note fetch_workouts emits
    sid: str | None = field(default=None, repr=False)   # outer sid if a non-empty str; names FDS files, never output

@dataclass(frozen=True)
class SportRows:
    rows: list[SportRow]                  # in service order; out-of-window and deleted rows are absent
    end_notes: list[str]                  # the page-cap and stalled-cursor notes, after every row's

def sport_rows(t: Transport, start_epoch_s: int, end_epoch_s: int) -> SportRows: ...
def to_workout(value: dict) -> Workout | None: ...   # renamed from _to_workout, body unchanged
```

  - `sport_rows` takes over the request (same `data_obj`, `list_key`, `max_pages`) and the per-row loop of `fetch_workouts`, including T3 Task 7's shared row decoder and its note texts.
  - `fetch_workouts` does this, in order:
    1. Per row: a `None` value appends `row.note`.
    2. Otherwise it appends the value to `raw`, then appends a `to_workout` record or the existing "Skipped a workout without a usable start time." note.
    3. Finally it extends the notes with `end_notes`.

    That gives the same note order as today.
- Produces, in `domains/workout_detail.py` (all Mi field names stay in this module):
  - `_DISTANCE_DIVISOR = {22: 10, 23: 1}` (`getDistanceRecordTimes`); its keys are the supported formats.
  - `def to_samples(record_raw: dict, proto: int) -> list[WorkoutSample]`.
  - `def to_track(gps_raw: dict) -> tuple[list[TrackPoint] | None, int]` returns the track and the number of points it left out.
  - Rules (§8 table):
    - **Sample time:** `epoch_s = segment["resumeTimestamp"] + i`.
    - **Heart rate:** `heartRate` of `0`, or absent, gives `None`.
    - **Distance:** `distance_m` is the running sum of `newDistance / _DISTANCE_DIVISOR[proto]`, as a float, across segments. A point without `newDistance` gives `None` and adds nothing.
    - **Steps:** a running sum of `newSteps` for format 22 only; `None` otherwise or when absent.
    - **Cadence and pace:** `cadence_spm` and `pace_s_per_km` come from format 22 only, and `0` gives `None`.
    - **Speed:** `speed_mps = speed / 36` for format 23 only. `0` gives `0.0`; absent gives `None`.
    - **Altitude:** `altitude_m` starts each segment at `initialAltitude`.
      - When both `isAltitudeRising` and `altitudeChange` are present, the running value moves by `(+1 if rising else -1) * altitudeChange / 10` and becomes the sample's altitude.
      - Otherwise the sample's altitude is `None` and the running value stays.
      - A non-finite `initialAltitude` gives `None` for the whole segment.
    - **Track positions:** `None` (the whole track is unusable, D23) when any point lacks `time`, `longitude` or `latitude`.
    - **Dropped points:** a point is left out and counted when its latitude or longitude is not finite, `|latitude| > 90`, `|longitude| > 180`, or `(latitude, longitude) == (0.0, 0.0)`.
    - **Track points:** `TrackPoint(epoch_s=time, latitude_deg=latitude, longitude_deg=longitude)`, with no rounding.

- [ ] **Step 1: Write the failing tests.**
  - **`test_workout_detail_mapping.py`.** Inputs are hand-written A dicts; segments are built with a local `seg(resume, points, initial_altitude=50.0)` helper.
    - `test_sample_time_is_segment_resume_plus_index`: segments at `T` (2 points) and `T + 100` (1 point) give epochs `[T, T + 1, T + 100]`.
    - `test_run_distance_is_cumulative_metres_from_decimetres`: `newDistance` 25, 30, 0 gives `2.5, 5.5, 5.5`, continuing across a second segment.
    - `test_ride_distance_is_cumulative_metres`: 5, 6 give `5.0, 11.0`.
    - `test_steps_are_cumulative_and_run_only`: format 22, `newSteps` 3, 2 give `3, 5`. Format 23 gives `None`.
    - `test_heart_rate_zero_becomes_none`.
    - `test_cadence_and_pace_zero_become_none_and_are_run_only`.
    - `test_speed_converts_tenth_kmh_and_keeps_zero`: 360 gives `10.0`, 0 gives `0.0`, absent gives `None`, and format 22 gives `None`.
    - `test_altitude_accumulates_from_each_segment_start`: `(rising 1, change 5)` then `(rising 0, change 3)` give `50.5` and `approx(50.2)`. The next segment, with `initialAltitude` 80.0, starts again from 80.0.
    - `test_altitude_without_a_valid_change_is_none_and_not_accumulated`.
    - `test_non_finite_initial_altitude_gives_none_for_the_segment`: NaN and inf.
    - `test_absent_field_gives_none_for_every_sample`: no point has `newDistance`, so every `distance_m is None`.
    - `test_track_maps_latitude_and_longitude_in_order`: `{"time": T, "longitude": 20.5, "latitude": 10.5}` gives `TrackPoint(T, 10.5, 20.5)`.
    - `test_track_drops_unusable_positions_and_counts_them`: (0, 0), latitude 91, longitude −181, a NaN and an inf give 5 dropped, and the good points are kept.
    - `test_track_is_none_when_positions_are_marked_invalid`: a point without `latitude` gives `(None, 0)`.
    - `test_empty_files_give_empty_lists`: `to_samples({"header": {}, "segments": []}, 22) == []` and `to_track({"header": {}, "gpsPoints": []}) == ([], 0)`.
    - `test_epoch_s_ignores_the_machine_zone`: parametrized over `TZ` `UTC` and `America/New_York` via `monkeypatch.setenv` plus `time.tzset()`, with `skipif(not hasattr(time, "tzset"))`. The results are identical.
    - `test_b_models_hold_only_verified_fields`:
      - the field names of `WorkoutSample` are `["epoch_s", "heart_rate_bpm", "distance_m", "steps", "cadence_spm", "pace_s_per_km", "speed_mps", "altitude_m"]`;
      - `TrackPoint` has `["epoch_s", "latitude_deg", "longitude_deg"]`;
      - `WorkoutDetail` has `["workout", "samples", "track"]`.
  - **`test_domain_workouts.py`**, appended:
    - `test_sport_rows_carry_the_outer_sid`: a fixture row's `sid` is on its `SportRow`.
    - `test_sport_rows_keep_skipped_rows_in_place`: rows in the order good, not-JSON, good give `[value, None (with the not-JSON note), value]`.
    - `test_sport_row_sid_must_be_a_non_empty_string`: an outer `sid` of `123`, `""` or absent gives `sid is None`.
    - `test_sport_row_repr_hides_the_sid`.
    - `test_notes_keep_their_order`: rows not-JSON, no-start, not-JSON, with `stalled=True`, give exactly `[NOT_JSON, NO_START, NOT_JSON, stalled_note("workout rows")]`.
- [ ] **Step 2: Run** `python -m pytest tests/test_workout_detail_mapping.py tests/test_domain_workouts.py -q`. Expected: the new tests FAIL, and the existing workouts tests pass.
- [ ] **Step 3: Implement** the models, the mapping functions (module docstring: §8 rules, the A-only list of §8, and the reason for each), and the `workouts.py` refactor.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. The existing tests in `tests/test_domain_workouts.py` are unchanged; `git diff` shows only additions to that file.
- [ ] **Step 5: Commit** `git add src/mifit/models/workout_detail.py src/mifit/domains/workout_detail.py src/mifit/domains/workouts.py tests/test_workout_detail_mapping.py tests/test_domain_workouts.py && git commit -m "feat(models,domains): workout detail models and mapping; share the sport-row read"`

---

### Task 5: The `workout_detail` domain

**Files:**
- Modify: `src/mifit/domains/workout_detail.py`, `src/mifit/chunks.py` (one `DOMAIN_CHUNK` entry and its docstring bullet), `tests/test_chunks.py` (`test_raw_point_domains_use_week` only), `tests/fds_fixtures.py` (row helpers)
- Create: `tests/test_domain_workout_detail.py`

**Interfaces:**
- Consumes: `sport_rows`, `to_workout` (Task 4); `FileRef`, `RECORD`, `GPS`, `server_key` (Task 1); `request_urls`, `FdsEntry`, `FdsDownloader`, `FdsUnavailable` (Task 3); `decrypt_body`, `FdsDecryptError` (Task 1); `parse_header`, `FdsFormatError`, `LAYOUTS`, `decode` (Task 2); `to_samples`, `to_track` (Task 4).
- Produces: `_MAX_WORKOUTS = 10` and `@register("workout_detail") def fetch_workout_detail(t: Transport, start_epoch_s: int, end_epoch_s: int, *, downloader: FdsDownloader | None = None, track: bool = True) -> DomainResult`. A `downloader` of `None` builds `FdsDownloader()`.
- Flow (§5). `n` is the workout's 1-based position in `records`, and `kind` is `"record"` or `"GPS"`. For each `row` of `sport_rows(t, start, end).rows`, newest first:
  1. **Skipped row.** `row.value is None` appends `row.note` and moves on.
  2. **Summary.** `workout = to_workout(row.value)`. When it is `None`, append `"Skipped a workout without a usable start time."` and add nothing to `records` or `raw` (Decision 1).
  3. **Record first.** Append `WorkoutDetail(workout, None, None)` and `{"workout": row.value, "record": None, "gps": None}`, then fill them in. `records[i]` and `raw[i]` always belong together.
  4. **Checks, in this order.** Each failure gives its note, and no request is sent:
     - `proto = as_int(value["proto_type"])` must be in `_DISTANCE_DIVISOR`;
     - `time = as_int(value["time"])` with `0 < time < 2**32`;
     - `tz15 = as_int(value["timezone"])` in `-128..127`;
     - `row.sid` must not be `None`.
  5. **Cap.** Once `_MAX_WORKOUTS` workouts have passed step 4, any later one only counts toward the cap note.
  6. **Links.** `refs = [FileRef(sid, time, tz15, proto, RECORD)] + ([FileRef(..., GPS)] if track else [])`, then `entries = request_urls(t, sid, refs)`. An `ApiError` gives a note and the next workout. `AuthError` and `OSError` propagate.
  7. **Each file:**
     - **Entry.** `entry = entries.get(server_key(ref))`. No entry, or `key_b64 is None`, gives the "no file" note and no download.
     - **Method.** A `method` that is not `None` and whose `.upper()` is not `"GET"` gives a note and no download.
     - **Download and decrypt.** `body = downloader.fetch(entry.url)` (on `FdsUnavailable`, a note), then `plain = decrypt_body(body, entry.key_b64)` (on `FdsDecryptError`, a note).
     - **Header.** `header = parse_header(plain, ref.file_type)` (on `FdsFormatError`, a note). Then `(header.timestamp, header.timezone, header.proto_type) != (time, tz15, proto)` gives the mismatch note and `None`.
     - **Layout.** `layout = LAYOUTS.get((proto, ref.file_type, header.version))`. When it is `None`, or `header.mode != 0`, or `header.validity is None`, `raw["record"]` or `raw["gps"]` becomes `{"header": header.as_raw()}` with the version or mode note, and B stays `None`.
     - **Decode.** `decoded = decode(plain, header, layout)`. `FdsFormatError` gives the damaged note, and that file stays `None` (no half data). `decoded.trailing_bytes > 0` adds the trailing note.
  8. **Mapping.** The decoded record goes through `samples = to_samples(...)`. The decoded GPS goes through `to_track(...)`: `None` gives the "no positions" note, and a dropped count above zero gives the dropped note. `raw["record"]` / `raw["gps"]` hold `decoded.raw`.
- After the loop, if any workouts were over the cap, add the cap note.
- **Note texts.** These are exact, and tests match them in full:
  - `f"Workout {n}: detail is not supported for this sport yet (format {proto})."`
  - `f"Workout {n}: its summary has no usable {what}, so its detail files cannot be named."`, where `what` is `format number`, `report time` or `time zone`.
  - `f"Workout {n}: its row has no source id, so its detail files cannot be named."`
  - `f"Workout {n}: could not get the download links ({exc})."`
  - `f"Workout {n}: the service has no {kind} file for it."`
  - `f"Workout {n}: the {kind} file's link is not a download (GET) link, so it was not fetched."`
  - `f"Workout {n}: the {kind} file could not be downloaded: {exc}."`
  - `f"Workout {n}: the {kind} file could not be decrypted."`
  - `f"Workout {n}: the {kind} file's header does not match the workout summary, so it was not read."`
  - `f"Workout {n}: format version {v} of the {kind} file is not supported yet."`
  - `f"Workout {n}: the {kind} file uses storage mode {m} (compressed or encrypted), which is not supported yet."`
  - `f"Workout {n}: the {kind} file is damaged ({exc}), so it was not read."`
  - `f"Workout {n}: ignored {count} trailing byte(s) at the end of the {kind} file."`
  - `f"Workout {n}: the GPS file marks its times or positions invalid, so there is no track."`
  - `f"Workout {n}: left out {count} track point(s) without a usable position."`
  - `f"Read the detail of the newest {_MAX_WORKOUTS} supported workouts only; {count} older one(s) in the range have no samples or track: ask for a shorter range."`
- **Chunk size.** `chunks.DOMAIN_CHUNK["workout_detail"] = "week"`, and the domain is not in `OLDEST_FIRST` (§7.3). Registering the domain makes T3's `test_every_registered_domain_has_a_chunk_size` require the entry in this same task.
- Produces, in `tests/fds_fixtures.py`:
  - `sport_value(*, proto, sport, time, start_time, timezone=32, **figures) -> dict`. It has M4's keys (`end_time`, `duration`, `distance`, `calories`, `avg_hrm`, `max_hrm`, `steps`, `version`) with small synthetic defaults.
  - `sport_row(value, *, sid=SYNTH_SID) -> dict`, the outer row `{"sid", "key": "sport_record", "category": "sport", "time": value["start_time"], "value": json.dumps(value), "zone_offset": value["timezone"] * 900}`.
  - `file_key(value, file_type, sid=SYNTH_SID) -> str`, which is `server_key(FileRef(sid, value["time"], value["timezone"], value["proto_type"], file_type))`.

- [ ] **Step 1: Write the failing tests in `test_domain_workout_detail.py`.**
  - **The world:**
    - `RUN` is `sport_value(proto=22, sport=1, time=T, start_time=T)`.
    - `RIDE` is `sport_value(proto=23, sport=6, time=U - 1, start_time=U)`.
    - `GYM` is `sport_value(proto=8, sport=308, ...)`.
    - Files: a v8 run record (`RUN_FLAGS_LIVE`, 2 points), a run GPS file (2 points), a v5 ride record (`RIDE_FLAGS_LIVE`, 2 points) and a ride GPS file, all keyed by `file_key`.
    - Transport and downloader: `FdsTransport([sport_row(RUN), sport_row(RIDE), sport_row(GYM)], service)` and `downloader=service`.
  - **Tests:**
    - `test_workout_detail_domain_is_registered`.
    - `test_reads_the_list_like_workouts`: `transport.calls[0]` equals the call `fetch_workouts` makes for the same window (`start_time = start - 1`, `list_key="sport_records"`, `max_pages=20`).
    - `test_requests_links_with_the_signing_path_rule`: each `link_calls` entry has `sign_path == "/service/gen_download_url"` and `base_path == "healthapp/"`. The run's `data_obj == {"did": SYNTH_SID, "items": [{"timestamp": T, "suffix": suffix(<run record ref>)}, {"timestamp": T, "suffix": suffix(<run GPS ref>)}]}`.
    - `test_one_link_request_per_supported_workout`: 2 link calls, and `service.fetched` has 4 URLs.
    - `test_maps_a_run_and_a_ride`:
      - `records[i].workout == to_workout(value)`;
      - the run's samples have `steps`, `pace_s_per_km` and `cadence_spm` set and `speed_mps is None`;
      - the ride's samples have `speed_mps` set and `steps is None`;
      - both tracks have 2 `TrackPoint`s, and the first has `latitude_deg == f32(10.0001)` and `longitude_deg == f32(20.0001)`;
      - `raw[i]["record"]["segments"][0]["dataPoints"][0]` holds the stored `newDistance` (25 for the run);
      - the only note is GYM's.
    - `test_files_are_named_by_the_report_time_not_the_start`: the ride's request `timestamp == U - 1`, and its first sample `epoch_s == U - 1`.
    - `test_unsupported_sport_sends_no_request`: with GYM alone, `records == [WorkoutDetail(to_workout(GYM), None, None)]`, no link call and no fetch, and `notes == ["Workout 1: detail is not supported for this sport yet (format 8)."]`.
    - `test_summary_without_usable_report_time_or_zone_is_noted`, parametrized over `time` absent, `"x"`, `2**32` and `timezone` 200. Each gives no request and the matching `what`.
    - `test_row_without_sid_is_noted`.
    - `test_track_false_requests_only_the_record_file`: one item per link call, no GPS fetch, `track is None`, `raw["gps"] is None`, and no note mentions GPS.
    - `test_missing_file_is_not_downloaded`: without a run GPS file, there is no fetch for its URL, the note is `"Workout 1: the service has no GPS file for it."`, and the samples are still there.
    - `test_link_request_error_affects_only_that_workout`: `link_errors={0: ApiError("Request failed with HTTP 500.")}` gives the run `None, None` plus a note containing `"HTTP 500"`, and the ride is read in full.
    - `test_auth_error_propagates`: `link_errors={0: AuthError("x")}` raises `AuthError`.
    - `test_download_failure_gives_none_and_note`: `fetch_errors={<run GPS>: FdsUnavailable("HTTP 404")}` gives `track is None`, and the note ends `"could not be downloaded: HTTP 404."`.
    - `test_link_that_is_not_get_is_not_fetched`: `entry_overrides` `method: "PUT"`.
    - `test_undecryptable_file_gives_none_and_note`: `entry_overrides` `obj_key: b64url(bytes(16))`. The record is `None` and raw `"record"` is `None`.
    - `test_header_mismatch_is_not_parsed`: a record file whose header time is `T + 5`.
    - `test_unsupported_version_keeps_only_the_header`: a v9 run record gives `raw["record"] == {"header": {...version 9...}}`, `samples is None`, and a note containing `"format version 9 of the record file"`.
    - `test_compressed_file_is_not_parsed`: mode 1.
    - `test_damaged_file_gives_none_and_note`: a record cut inside a point gives `raw["record"] is None`, `samples is None`, a `"damaged"` note, and the GPS still read.
    - `test_trailing_bytes_are_noted`.
    - `test_gps_without_valid_positions_has_no_track`: GPS flags `0x7F` give `raw["gps"]["gpsPoints"]` present, `track is None`, and the "no track" note.
    - `test_dropped_track_points_are_counted`: one (0, 0) point gives the note with `1`.
    - `test_cap_reads_only_the_newest_ten_supported_workouts`:
      - setup: 12 RUN rows one hour apart, plus GYM at the oldest end;
      - 10 link calls;
      - 13 records, whose last three are `None/None`;
      - exactly one cap note, containing `"2 older"`;
      - GYM has its own note.
    - `test_records_and_raw_are_index_aligned`: `len(records) == len(raw)`, and `raw[i]["workout"] is <the decoded value of row i>`.
    - `test_workout_without_start_gives_no_record`: the workouts note text, and no raw entry.
    - `test_output_never_contains_link_secrets`. Serialize `{"records": [asdict(r) for r in records], "raw": raw, "notes": notes}` with `json.dumps`. It must not contain any of:
      - `SYNTH_SID`, `URL_MARK`, `"SYNTH-OBJ-NAME"`, `SYNTH_KEY_B64`, `"fds.api.xiaomi.com"`, `str(EXPIRES)`;
      - any `suffix(...)` of the four refs.
    - `test_failures_do_not_echo_secrets_in_notes`: with every failure path above triggered once, no note contains `URL_MARK` or `SYNTH_SID`.
  - **`test_chunks.py`:** in `test_raw_point_domains_use_week`, the week set becomes `{"heart_rate", "workout_detail"} | ({"spo2"} if "spo2" in REGISTRY else set())`. Rename the test `test_chunk_size_per_domain`.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_workout_detail.py tests/test_chunks.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `fetch_workout_detail` and the chunk entry.
  - The module docstring sums up:
    - the chain (§5);
    - the request budget;
    - the cap;
    - `None` vs `[]`;
    - how notes number workouts (within a chunk for a chunked read, where notes carry the chunk label);
    - which fields stay A-only;
    - that nothing is cached.
  - `chunks.py`'s docstring gains the bullet: "`workout_detail` uses `week`: each workout costs up to three requests and a call reads at most 10".
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/domains/workout_detail.py src/mifit/chunks.py tests/fds_fixtures.py tests/test_domain_workout_detail.py tests/test_chunks.py && git commit -m "feat(domains): workout_detail domain (record + GPS via FDS)"`

---

### Task 6: Facade, package exports, CLI `--no-track`, chunk options

**Files:**
- Modify: `src/mifit/client.py`, `src/mifit/__init__.py`, `src/mifit/chunks.py` (`read_chunks`), `src/mifit/cli/__init__.py`, `src/mifit/cli/partial.py` (`export_header`, the mismatch message)
- Test: `tests/test_client.py`, `tests/test_cli.py`, `tests/test_chunks.py`, `tests/test_cli_partial.py`

**Interfaces:**
- Consumes: `fetch_workout_detail`, `FdsDownloader`, `WorkoutDetail`/`WorkoutSample`/`TrackPoint`; T3's `read_chunks`, `export_header`, `load_partial`, `PartialError` and `_cmd_export`.
- Produces in `client.py`:
  - `MiFit.__init__(..., fds_http: Any = None)`, stored as `self._fds_http`. Docstring: the object-store downloads use it in place of `requests`, for tests. It never sees the session.
  - `MiFit.workout_detail(self, start: DateArg, end: DateArg, *, track: bool = True) -> DomainResult`. It returns `fetch_workout_detail(self._transport, to_epoch_s(start), to_epoch_s(end, end_of_day=True), downloader=FdsDownloader(http=self._fds_http), track=track)`.
    - The docstring gives the §7.1 example, including the single workout `client.workout_detail(w.start_epoch_s, w.start_epoch_s)`.
    - It says `track=False` skips the GPS file, and that a track shows where you were.
    - It mentions the 10-workout cap.
  - `MiFit.read_chunks(..., options: Mapping[str, Any] | None = None)` passes `options` through.
- `chunks.read_chunks(..., options: Mapping[str, Any] | None = None)` calls `read(first, last, **dict(options or {}))` for every chunk. The docstring says `options` are keyword arguments for the domain method, such as `{"track": False}` for `workout_detail`.
- `__init__.py` exports `WorkoutDetail`, `WorkoutSample` and `TrackPoint`, and adds them to `__all__`.
- CLI (`cli/__init__.py`):
  - **Constants and helpers:**
    - `_TRACK_DOMAINS = frozenset({"workout_detail"})`;
    - `def _domain_options(args) -> dict[str, Any]` returns `{"full": args.full}` for `_FULL_DOMAINS`, `{"track": not args.no_track}` for `_TRACK_DOMAINS`, and `{}` otherwise;
    - `_fetch(client, domain, start, end, options: dict[str, Any]) -> DomainResult` returns `getattr(client, domain)(start, end, **options)`.

    Every `_fetch` call site, single-chunk and chunked, passes `_domain_options(args)`. That keeps `heart_rate`'s `full=` exactly as before.
  - **The flag:** `--no-track` (`store_true`, help `"workout_detail only: leave out the GPS track (no GPS file is requested)"`).
  - **The ignored note:** next to the `--full` note, `note: --no-track only affects workout_detail; ignored for <domain>.`
  - **The partial header:** for `workout_detail`, the header passed to `start_partial`/`load_partial` is `export_header(domain, start, end, size, track=not args.no_track)`. Every other domain's header is unchanged.
  - **The docstring:** the domain list gains "workout detail", and the module docstring notes that `--no-track` leaves the track out of the output and the partial.
- `cli/partial.py`:
  - `export_header(domain, start, end, chunk, **options: Any) -> dict` appends the options after `chunk`.
  - `load_partial`'s mismatch message names every stored header field except `mifit_partial`, so `track` shows up.

- [ ] **Step 1: Write the failing tests.**
  - **`test_client.py`:**
    - `test_workout_detail_delegates_with_bounds_downloader_and_track`, a spy on `client_module.fetch_workout_detail`. With `MiFit(session, http=..., fds_http=sentinel)`:
      - the transport is the fake;
      - the bounds are local day bounds, as in the workouts test;
      - `downloader._http is sentinel`;
      - `track=False` passes through.
    - `test_workout_detail_by_epoch_reads_one_workout`: `client.workout_detail(1790820000, 1790820000)` passes `start == end == 1790820000`.
    - `test_default_downloader_uses_requests`: without `fds_http`, `downloader._http is requests`.
    - `test_read_chunks_forwards_options`: monkeypatch `client_module.chunks.read_chunks` to capture its keyword arguments. Then `options={"track": False}` arrives unchanged.
    - `test_importing_client_registers_the_domains`: the expected list gains `'workout_detail'` between `'weight'` and `'workouts'`.
    - `test_workout_detail_is_registered_once_client_is_imported`.
    - `test_package_exports`: the three new names.
  - **`test_chunks.py`:** `test_read_chunks_forwards_options_to_every_chunk`.
    - Setup: `import mifit.client`, so `workout_detail` is registered, and a stub client whose `workout_detail(self, start, end, **kw)` records its calls.
    - Over `2026-10-01..2026-10-14` with `options={"track": False}`, it records 2 calls (newest week first), each with `{"track": False}`.
    - Without `options`, the kwargs are `{}`.
  - **`test_cli_partial.py`:**
    - `test_header_carries_extra_export_options`: `export_header("workout_detail", "2026-10-01", "2026-10-14", "week", track=False)` has `"track": False` after `"chunk"`.
    - `test_load_names_a_differing_track_setting`: written with `track=False`, loaded with `track=True`, it raises `PartialError` whose message contains `"track"`.
  - **`test_cli.py`:**
    - Fake setup:
      - Add `DETAIL = WorkoutDetail(workout=WORKOUT, samples=[WorkoutSample(1790820000, 120, 2.5, 3, 170, 360, None, 50.5)], track=[TrackPoint(1790820000, 10.0, 20.0)])`.
      - Add `FakeMiFit.workout_detail(self, start, end, *, track=True)`, which records `("workout_detail", start, end, {"track": track})` and returns `DomainResult(records=[DETAIL], raw=[{"workout": {"sport_type": 1}, "record": None, "gps": None}], notes=[])`.
      - Add a `detail_echo(fail_at=None, error=None)` helper, modeled on T3's `echo`.
    - `test_export_workout_detail_writes_nested_records`: `records == [asdict(DETAIL)]`, and the calls are `[("workout_detail", "2026-10-01", "2026-10-02", {"track": True})]`.
    - `test_export_workout_detail_no_track`: the call carries `{"track": False}`.
    - `test_no_track_is_ignored_for_other_domains`: `steps --no-track` exits 0, the call has `{}`, and stderr has `"--no-track"`.
    - `test_chunked_workout_detail_passes_track_to_every_chunk`: `--from 2026-10-01 --to 2026-10-14 --no-track --json out` makes 2 calls, `2026-10-08..2026-10-14` then `2026-10-01..2026-10-07`, both with `{"track": False}`.
    - `test_partial_header_records_the_track_setting`: with a failure at the second chunk, the partial's first line is `{"mifit_partial": 1, "domain": "workout_detail", "from": "2026-10-01", "to": "2026-10-14", "chunk": "week", "track": false}`.
    - `test_resume_with_a_different_track_setting_is_refused`: after that failure, a rerun with `--resume` and without `--no-track` exits 1, stderr has `"track"`, and the partial is byte-identical.
    - The existing `test_export_heart_rate_full_flag` and T3's partial-header tests stay unchanged and must still pass: `full=` and the five-key header of other domains are untouched.
- [ ] **Step 2: Run** `python -m pytest tests/test_client.py tests/test_cli.py tests/test_chunks.py tests/test_cli_partial.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** everything above.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. Then `python -m pip install -e . && mifit export --help` lists `workout_detail` and `--no-track`.
- [ ] **Step 5: Commit** `git commit -m "feat(client,cli,chunks): expose workout_detail (--no-track, weekly chunks)"` with the files above. The README is left for Task 7.

---

### Task 7: Local real-file gate (controller, before push) + close-out docs

**Files:**
- Create: `notes/12-fds-workout-detail.md`; `work/m6/scripts/gate_m6.py` (git-ignored, never committed)
- Modify: `README.md`, `docs/superpowers/roadmap.md`

- [ ] **Step 1 (controller, local only, D25): run the real-file gate before any push.**
  - **Where it runs.** Write `work/m6/scripts/gate_m6.py` and run it with `python -I work/m6/scripts/gate_m6.py` from the repo root.
  - **Setup.** The script inserts `<repo>/src` into `sys.path`. It replaces `socket.socket.connect`, `socket.create_connection` and `socket.getaddrinfo` with a function that raises, so no network is possible.
  - **Inputs** (all git-ignored):
    - `work/m6/out/list.json` (the list answer);
    - `work/m6/out/urls_meta.json` (`dl<n>` → row, file type);
    - `work/m6/dec/dl<n>.raw` (plaintext files);
    - optionally `work/m6/dl/dl<n>.bin` and `work/m6/out/urls.json`.
  - **Part A, parse only.** For each of the four files:
    - `parse_header` matches the row's `time`/`timezone`/`proto_type`;
    - the `LAYOUTS` entry exists;
    - `decode` raises nothing, with `trailing_bytes == 0`.

    Then `to_samples`/`to_track`, with these checks:
    - **Steps (run):** last steps − `steps` == 0.
    - **Calories (ride):** Σ `newCalories` / `calories` == 1.000, computed from A.
    - **Distance:** last `distance_m` / `distance` is within 1 ± 0.002.
    - **Heart rate:** max bpm − `max_hrm` == 0, and min non-zero bpm − `min_hrm` == 0.
    - **Track length:** the Haversine sum over consecutive points ≤ 10 s apart, divided by `distance`, is within 0.95–1.06 (spec: 0.99 run, 1.02 ride).
    - **Dropped points:** 0.
    - **Altitude:** the differences of max/min altitude from `max_height`/`min_height` are printed rounded to 0.1 m. They are information for §16 Q4, not a gate.
  - **Part B, whole chain offline.** Only when the `.bin` files and `urls.json` exist:
    - `decrypt_body(dl<n>.bin, obj_key)` equals `dec/dl<n>.raw` for all four.
    - Run `fetch_workout_detail` over the window from the oldest to the newest row's outer `time`, with a gate transport and downloader:
      - the transport is a `Transport` subclass serving the list rows from `get_paged_result` and the saved `urls.json` payload from `get_json`;
      - the downloader is a real `FdsDownloader(http=<fake serving dl<n>.bin by URL>, delay_s=0)`.

      It must give two `WorkoutDetail`s with samples and track not `None`. The only notes allowed are the two "not supported (format 8)" ones. This proves the file names match the keys the service actually answered.
  - **Output.** Only booleans, counts and ratios: never a time, coordinate, health value, sid, URL or key.
  - **On failure:** do not push. Fix the table or mapping in Task 2/4 with a new synthetic test reproducing the shape, or record the deviation.
  - **If `work/m6/dec/` is gone:** either re-download with `work/m6/scripts/probe_fds.py` (≤ 6 read-only calls, ≥ 2 s apart, stop at a 401), or skip the gate and write "布局只经合成测试" into the roadmap result line.
  - **Check:** `git status --short` shows nothing under `work/`.
- [ ] **Step 2: Write `notes/12-fds-workout-detail.md`** in Chinese, like notes/10–11, with synthetic values only. It is the factual version of spec §4, with these sections:
  - §1 链路与请求预算;
  - §2 文件标识 (rule plus the Task 1 vector);
  - §3 `gen_download_url` (request, response, missing file);
  - §4 下载与解密;
  - §5 文件头与有效性标志长度表;
  - §6 record v8 / v5 布局与四维有效位 (with the two flag vectors);
  - §7 GPS v2 布局;
  - §8 SDK 映射 (A names, B table, A-only list);
  - §9 `schemas.zip` 的语义 (spec §4.10, for later versions);
  - §10 未决 (spec §16 Q1–Q10, by number).
- [ ] **Step 3: Update `README.md`** (T1 rule: the "What it reads today" table stays verified-only, so `workout_detail` is not added to it):
  - **Export.** The synopsis gains `[--no-track]`. The `<domain>` list gains `workout_detail` ("per-second samples and GPS track of outdoor runs and rides; not yet checked against the app"). Add the bullet "`--no-track`: `workout_detail` only; skip the GPS file".
  - **Python API.** Add `detail = client.workout_detail("2026-10-12", "2026-10-12")` and the `track=False` variant, with one line on `records` (`WorkoutDetail` → `WorkoutSample` / `TrackPoint`).
  - **Known limitations.** Add one bullet:
    - workout detail is new and pending a check against the app;
    - it covers outdoor runs and rides in the file formats seen so far (record 8 and 5, GPS 2), and other sports or formats are reported in `notes`;
    - at most 10 workouts per call (a week per chunk);
    - a GPS track shows where you were, including where you live, and `--no-track`/`track=False` leaves it out;
    - download links and keys never appear in the output.
  - Do not change the Install or Development sections, because `pip` brings `cryptography`.
- [ ] **Step 4: Update `docs/superpowers/roadmap.md`:**
  - **§0.** The M6 row becomes `✅ 实现完成（<date>），待真机核对`.
  - **§1.** Add `workout_detail：每次运动的逐秒样本（WorkoutSample）与 GPS 轨迹（TrackPoint），户外跑 / 骑（record v8 / v5、GPS v2）；待真机核对（notes/12）`, and update the test count to what `python -m pytest -q` prints.
  - **§3 M6.** Add a `结果（<date>）✅（待真机核对）` line:
    - the chain;
    - the new `cryptography` dependency;
    - the three layouts and their scope (D1);
    - the B fields, with altitude provisional (Q4) and the rest A-only;
    - the cap of 10 per call and `week` chunks;
    - `--no-track`;
    - the Task 7 gate outcome ("本机真实文件闸门通过：4/4 份文件走到末尾…" or "布局只经合成测试").
  - **§6.** The "M6 开工前" row is answered: the design was approved by the controller ruling, and the sport-type question moves to the end-of-run session (spec §16 Q9).
- [ ] **Step 5: Run** `python -m pytest -q`. Expected: everything passes. `git status --short` lists only `notes/12…`, `README.md` and the roadmap.
- [ ] **Step 6: Commit** `git add notes/12-fds-workout-detail.md README.md docs/superpowers/roadmap.md && git commit -m "docs: notes/12 FDS workout detail; README; roadmap M6 implemented, pending live check"`. The controller pushes after Step 1 passed.

---

### Task 8: Live app comparison (manual, with the user; deferred by the controller to the end-of-run session)

**Files:**
- Create: `docs/superpowers/verification/<run date>-workout-detail-live.md`
- Modify: `docs/superpowers/roadmap.md` (§0, §1, §3 M6); `README.md` (the "What it reads today" row, only for what passes); `src/mifit/models/workout_detail.py`, `src/mifit/domains/workout_detail.py` and tests only if a B field fails

- [ ] **Step 1:** Run `mifit export workout_detail --from <run day> --to <ride day> --json "$HOME\mifit-verify\detail.json"`, which writes outside the repo. At most two workouts in range keep the call to about 7 requests.
- [ ] **Step 2:** Compute the §14 figures with a script under `work/m6/` (never committed):
  - first and last track times (local);
  - moving track length;
  - a track SVG with **no base map**, opened locally only;
  - per-minute heart-rate means plus avg/max/min;
  - per-km pace (run) or max speed (ride);
  - cadence avg/max;
  - the number of sample gaps;
  - an altitude curve plus max/min;
  - the point count and interval histogram.
- [ ] **Step 3:** The user reads the matching figures from the app's workout detail page. Answer §16 Q1–Q10 with them, and ask Q9 (which sports they will record) and Q10 (whether opening the SVG is OK) first.
- [ ] **Step 4:** Apply the §14 pass criteria per row.
  - If row 8 (altitude) fails, move `altitude_m` out of `WorkoutSample`, so it stays A-only, and update its tests (D21).
  - A failing speed-zero answer (Q6) can turn `speed_mps` `0.0` into `None`.
  - Record each verdict as match/differ, with no values beyond what a row needs.
- [ ] **Step 5:** Update the roadmap (M6 ✅ or partial, §1) and the README table (a `workout_detail` row, `WorkoutDetail` → `WorkoutSample` / `TrackPoint`, only for what passed). Run `python -m pytest -q`, then commit `docs: workout detail live verification; roadmap M6`. Push after the user says yes.

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Notes number workouts by position, and `raw` is index-aligned with `records`.**
   - Each note starts `Workout <n>: `, where `n` is the 1-based position in `records`. The spec allows "a position or 'this workout'", and the position tells the caller which one is meant.
   - `raw[i]` always belongs to `records[i]`, so T3's merge keeps them aligned.
   - A row without a usable start therefore gives neither a record nor a `raw` entry, only the `workouts` note. `workouts` itself still keeps such a value in its `raw`.
2. **Workouts over the cap still get a record.** They get `WorkoutDetail(workout, None, None)` and a `raw` entry with no requests, plus one cap note. So every workout in the window is listed, and `None` always comes with a note (§7.1). Only workouts that would send requests count toward the 10, after the format, field and sid checks.
3. **Fixtures are packed from the spec's tables by hand, without importing `mifit.fds.layouts`.** A wrong table entry then fails a test instead of round-tripping silently (the spec's worry in D25). The local real-file gate (Task 7) remains the final check.
4. **The tables hold only the rows the enabled versions use.** The record v9 / v6+ rows (4-byte altitude, 2-byte distance) and the GPS v3 rows are left out, because no real file can check their bit layout (D1). `until` still marks where the v1 rows end, and the `layouts` docstring says what has to be added before a new `LAYOUTS` key.
5. **`sport_rows` keeps skipped rows in place.** It returns `SportRows(rows, end_notes)`, with each skipped row carrying its own note, so `workouts` keeps its exact note order. `SportRow.sid` is hidden from the repr. `_to_workout` becomes the public `to_workout`, since a second module now uses it.
6. **The downloader's default `http` is the `requests` module itself.** That gives a fresh session per download, as the app's `new OkHttpClient()` does, so no cookie jar can carry anything over. It sends exactly `timeout`, `allow_redirects=False` and `stream=True`. Its pause is its own copy of the transport formula, pinned by a test, which leaves the MIT-attributed `transport.py` untouched.
7. **A missing `method` counts as GET; a present non-GET method is refused.** The field only describes the link, but a different method means a link for something else.
8. **Trailing bytes are ignored with a note, for GPS and record alike.** For GPS that means fewer bytes than one point (§17 suggested this); for a record, fewer than one segment header. The decoded points are kept.
9. **A link answer without a `result` map raises `ApiError`, with no payload attached.** It is noted per workout like other `ApiError`s. The payload is left off because it could hold links.
10. **An unusable GPS position is detected from the decoded points.** If any point lacks `time`, `longitude` or `latitude`, the track is `None`. Validity is file-wide, so this equals checking the header bits, and it needs no extra API.
11. **The `.partial` header carries `track` for `workout_detail` exports.** T3's header-equality rule then refuses a resume with a different setting, and the mismatch message names it. Other domains' headers are unchanged.
12. **The CLI forwards domain keywords through `_domain_options(args)`.** `_fetch` takes that options dict, and the `read_chunks` parameter is named `options` (§7.3 left the name open).
13. **The chunk entry lands with the registration, in Task 5.** Registering the domain makes T3's "every registered domain has a size" test require it. The forwarding of `options` lands with the facade, in Task 6.
14. **Error types:** `FdsUnavailable(MiFitError)`, `FdsDecryptError(ValueError)` and `FdsFormatError(ValueError)`. All have fixed messages, and each replacement is raised outside its `except` block. The domain catches exactly these three, plus `ApiError` from the link request.
15. **Real files gate the push, in Task 7, beyond the spec's parse-only check.** The gate also decrypts the saved bodies with `decrypt_body`, and it runs the whole domain offline over the probe's saved answers. That proves the computed server keys are the ones the service answered. It runs under a socket guard and prints only booleans and ratios.
16. **The README "What it reads today" table stays verified-only (T1 rule).** `workout_detail` is listed in the export domain list, marked "not yet checked against the app", with `--no-track` and a privacy bullet. The table row waits for Task 8.
17. **No code is ported from Mi-Fitness-Sync.** The reference snapshot has no FDS module (D17). The attribution procedure is in Global Constraints in case an implementer ports something.

## Self-Review

1. **Spec coverage:**
   - §2 goals:
     - 1 (both sports, record + GPS): Tasks 2 and 5;
     - 2 (full A layer): Tasks 2 and 5;
     - 3 (verified B fields): Task 4;
     - 4 (safe links and keys, untrusted input): Tasks 1–3 and 5;
     - 5 (offline synthetic tests): every task.
   - Non-goals (other formats and versions as notes, no cache, T3 owning long ranges): Tasks 5 and 6.
   - Anti-goals: Global Constraints, with `test_output_never_contains_link_secrets`.
   - §3: pacing in Task 3; the signing path in Tasks 3 and 5; 401 handling in Task 5 (`AuthError` propagates, downloads never carry a session); `track=False` in Tasks 5 and 6.
   - §4: Tasks 1–3 for the facts and test vectors; §4.2's example is computed.
   - §5 flow: Task 5. §6 modules: the file map; every listed file has a task.
   - §7: Task 6, with the 10-workout cap in Task 5 and the T3 interplay (`week`, `options`, `.partial`) in Tasks 5 and 6.
   - §8: Task 4. §9: Tasks 2 and 5, with the low-level-use docstring in Task 3. §10: Task 1. §11: Tasks 1–3 and 5.
   - §12: the notes table in Task 5. §13 items 1–10: Tasks 1–6, plus licensing unchanged by Decision 17.
   - §14 / §16: Task 8. §15:
     - D1–D18: Global Constraints, the Decisions and Task 7 (`notes/12`);
     - D19–D20 (GPS speed and ride cadence stay A-only): Task 4;
     - D21 (altitude): Task 8;
     - D22: Task 1;
     - D23: Task 4;
     - D24: Task 6;
     - D25: Task 7.
   - §17 open points: Decisions 4, 6, 8 and 14; the missing-`dec/` fallback is in Task 7 Step 1. ✓
2. **Step scan.**
   - Every code step names its file, signature and pinned values (vectors, flags, sizes, caps, note texts).
   - Algorithm detail is given only for the nibble, validity and bit rules and the per-file flow, which the signatures do not settle.
   - Task 7 Step 1 and Task 8 are manual and say so. ✓
3. **Type consistency.** These names are used identically in every task:
   - `FileRef(sid, timestamp, tz15, proto, file_type)`, `RECORD`/`GPS`, `key_bytes`, `suffix`, `server_key`;
   - `decrypt_body`, `FdsDecryptError`;
   - `FileHeader(...).as_raw()`, `parse_header(plain, file_type)`, `validity_len`, `FdsFormatError`;
   - `LAYOUTS`, `selected_fields`, `decode`, `DecodedFile(raw, trailing_bytes)`;
   - `request_urls`, `FdsEntry(url, key_b64, method, expires_s)`, `FdsDownloader.fetch`, `FdsUnavailable`;
   - `SportRow(value, note, sid)`, `SportRows(rows, end_notes)`, `sport_rows`, `to_workout`;
   - `to_samples`, `to_track`, `fetch_workout_detail(..., downloader, track)`, `_MAX_WORKOUTS`;
   - `MiFit(fds_http=)`, `MiFit.workout_detail(..., track=)`, `read_chunks(..., options=)`, `export_header(..., **options)`, `_domain_options`;
   - the fixture names `FakeFdsService`, `FdsTransport`, `StoreHttp`, `file_key`, `sport_value`, `sport_row`. ✓
4. **Review Focus:** five lines, each pinned by named tests in Tasks 2, 3, 5 and 6. ✓
5. **Proportion:** eight tasks, and the only code blocks are the two interface sketches (`FileRef`, `SportRow`). Tests are given by name and assertion, and bodies are left to the implementer. In bytes the plan is about 1.5× the spec, which is written in denser Chinese. Most of the extra length is test vectors, flag constants and exact note texts, which the implementer cannot derive. ✓
