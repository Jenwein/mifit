# mifit Long-Range Export (T3: chunking, throttling, resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long and full-history exports safe and restartable. A range longer than one chunk is read chunk by chunk, serially, through the existing paced transport. Ranges over 92 days need an explicit `--long`. An interrupted export continues with `--resume` from a `<PATH>.partial` file. The same change folds in six cross-cutting backlog items (B1–B6 below).

**Architecture:** A new library module, `mifit.chunks`, owns the chunk policy: a size per domain, splitting a day range into fixed-length blocks counted from `--from`, the per-domain order, a lazy serial `read_chunks` generator, and `merge_chunks`. `MiFit.read_chunks` exposes it to SDK users. The CLI adds what only a command line needs: the `--long` opt-in, progress lines, the `.partial` store (`mifit.cli.partial`, JSON Lines, one line per completed chunk) and `--resume`. A range that fits in one chunk still makes exactly one domain call, the same one as before. Pacing is not new code: `Transport._send` already sleeps `delay_s` (1.0 s) plus up to 0.5 s of jitter before every request, including every page and the retry after a renewal. One `MiFit` serves the whole export, so its renewal budget (3 renewals, 1 per request) spans every chunk. Separately, `PagedResult` gains `stalled`, so a cursor the service breaks off is no longer silent.

**Tech Stack:** Python 3.12+, existing mifit modules (`Transport`, `PagedResult`, `DomainResult`, `REGISTRY`, `domains.days`), argparse, pytest. No new dependencies.

**Spec:**
- Roadmap `docs/superpowers/roadmap.md`: §4 T3 "默认分块（按周或按月）+ 串行 + 带抖动的延时（设计 §3 风控默认值），全量导出需要显式开启。断点续传（中断后从上次的块继续），`--full` 等分页上限触顶时给出明确提示". The `--full` cap note exists since M3, and the workouts cap note since M4.
- Design `docs/superpowers/specs/2026-10-13-mifitness-sdk-design.md`: §2 anti-goal "触发账号风控"; §3 风控 (串行不并发, ~1 s 带抖动, 按天/按周分块, `limit` ~50, "全量/长区间导出默认分块节流并可被显式开启"); §7 (registry, generic export CLI).
- Cross-cutting backlog from the program ledger, items B1–B6 (verdicts in Decisions):
  - **B1:** NaN/Infinity in the CLI's JSON output.
  - **B2:** a silent stop on a missing or repeated cursor.
  - **B3:** verify pacing.
  - **B4:** `raise … from None` that leaves `__context__` set.
  - **B5:** the shared fake transport.
  - **B6:** a failed sub-read sinks the whole call.

**Precondition:** M5 (`spo2_daily`, `spo2`, `weight`, `domains/raw_window.py`, `MiFit.profile`, `mifit profile`) is being implemented on `m5-spo2-weight-profile` and is expected in `main` before Task 1. Rows marked **(M5)** apply only to files and domains that exist; skip any that do not. Work goes onto a short branch (`t3-long-range-export`) that is fast-forwarded into `main`. Pushes are pre-approved per milestone and done by the controller.

## Global Constraints

- **Read-only, no live calls.** No task talks to the service. Tests use fakes; `tests/conftest.py` refuses the network and moves HOME under `tmp_path`.
- **Serial, paced, never concurrent.** No threads, no async, no pool. Every request goes through `Transport._send`, whose pause is `delay_s + random.uniform(0, 0.5 * delay_s)` with the default `delay_s = 1.0`. No task changes these values.
- **Chunk sizes:**
  - Fixed blocks counted from `--from`: `day` = 1 day, `week` = 7 days, `month` = 31 days.
  - Per domain: `heart_rate` and `spo2` (M5) use `week`; `steps`, `heart_rate_daily`, `consumption`, `sleep`, `workouts`, `spo2_daily` (M5) and `weight` (M5) use `month`.
  - A domain missing from the table uses `week`.
- **Long-export opt-in:** a CLI range of more than **92** days (`--to − --from + 1 > 92`) needs `--long`. The one exception is `heart_rate --full`, which ignores `--from`.
- **Backward compatible:**
  - These are unchanged: domain names, every `MiFit` domain method and its single-call behavior, `fetch_*` signatures, `Transport.get_paged`'s signature and return value, positional `PagedResult(items, truncated)`, the CLI output shape `{records, raw, notes}`, and the existing flags.
  - A CLI range that fits in one chunk sends exactly the requests it sent before.
- **No secrets in the partial file.** It holds the export's own data (the same data as the output) plus `domain`, `from`, `to`, `chunk` and a format number. It never holds a token, cookie, ssecurity, user id or session field.
- **No secrets or real health data in the repo.** Tests use synthetic values only.
- **A + B layers stay as they are.** Chunking never changes a record, only which window produced it.
- **Tests stay offline and green after every task:** `python -m pytest -q`. The baseline is what it reports before Task 1: 490 passed on `21ab4bc`, plus M5's tests once M5 is merged.
- **Line endings:** files are LF. Python `write_text` on this machine writes CRLF, so normalize after any scripted edit.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **An export interrupted part-way** (a network error, a 401 the renewal cannot fix, Ctrl+C): completed chunks survive, `--resume` reads only the remaining ones, and the final JSON equals an uninterrupted run's. → Task 6: `test_failed_chunk_keeps_completed_chunks_and_points_to_resume`, `test_resume_reads_only_remaining_chunks`, `test_auth_error_mid_export_keeps_progress`, `test_ctrl_c_mid_export_keeps_progress`.
- **A row exactly on a chunk boundary** (a raw sample at a block's first local midnight, or the daily report of a block's first or last day) appears exactly once. → Task 3 `test_read_chunks_matches_single_read[*]`.
- **`--resume` with arguments that differ from the stored export** (other dates, another `--chunk`, another domain) is refused and never merged. → Task 5 `test_load_rejects_another_exports_header`; Task 6 `test_resume_with_different_arguments_is_refused`.
- **A partial file whose last line was cut off** (power loss mid-append): that chunk is read again, with no crash and no half chunk. → Task 5 `test_load_drops_a_torn_last_line`.
- **A raw payload holding NaN or Infinity:** the output is strict JSON, with `null` in those places and a note saying so. → Task 4 `test_export_replaces_non_finite_numbers_with_null`.

---

### Task 1: Exception chains carry nothing from the original (B4)

**Files:**
- Create: `tests/support.py`
- Modify:
  - `src/mifit/transport.py` (the decrypt block in `get_json`)
  - `src/mifit/auth/session.py` (the `json.loads` in `load_session`)
  - `src/mifit/client.py` (`MiFit.__init__`'s `load_session` call)
  - `src/mifit/cli/__init__.py` (`_open_client`)
- Test: `tests/test_transport.py`, `tests/test_session_store.py`, `tests/test_client.py`, `tests/test_auth_renew.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `tests/support.py: def exception_chain(exc: BaseException) -> list[BaseException]`. It is moved verbatim from `tests/test_client.py`. `test_client.py` and `test_auth_renew.py` import it and delete their local copies.
- Pattern, as in `auth/renew.py`: inside the `except` block only set a local (a flag or the new exception); raise after the block. Messages and exception types stay unchanged. The replaced exception then has `__cause__ is None`, `__context__ is None` and `__suppress_context__ is False`.

- [ ] **Step 1: Write the failing tests:**
  - `test_transport.py::test_unparsable_body_error_has_no_chain`. Encrypt the invalid JSON `b'{"code": 0, "x": "SYNTH-BODY-FRAGMENT"'` with the request's `_nonce`, the same way `encrypt_body` does but without `json.dumps` (a small `FakeHttp` subclass). Assert:
    - `get_json` raises `ApiError`;
    - `exception_chain(exc) == [exc]`;
    - `"SYNTH-BODY-FRAGMENT" not in str(exc)`.
  - Add `assert exception_chain(excinfo.value) == [excinfo.value]` to `test_undecryptable_body_raises_ApiError_without_leaking_body`.
  - `test_session_store.py::test_corrupt_file_error_has_no_chain`. Write `'{"service_token": "SYNTH-TOKEN-CORRUPT",'` to a session file. `load_session(path)` raises `ValueError` with `exception_chain(exc) == [exc]`, and the token appears in neither `str(exc)` nor `exc.args`.
  - `test_client.py::test_corrupt_stored_session_raises_AuthError`: replace `assert excinfo.value.__suppress_context__ is True` with `assert exception_chain(excinfo.value) == [excinfo.value]`.
  - `test_client.py::test_corrupt_session_file_leaves_no_chain`. Write that same corrupt text to `default_session_path()` under the isolated home. `MiFit()` raises `AuthError`, and its chain is just itself.
  - `test_cli.py::test_open_client_value_error_leaves_no_chain`. Set `FakeMiFit.error_on_init = ValueError("session file x is not valid JSON")`. `cli._open_client()` raises `AuthError`, and its chain is just itself.
- [ ] **Step 2: Run** `python -m pytest tests/test_transport.py tests/test_session_store.py tests/test_client.py tests/test_cli.py -q`. Expected: the new chain assertions FAIL.
- [ ] **Step 3: Implement** the pattern at the four sites. Rewrite each site's comment to say why: a `JSONDecodeError` keeps the whole document in `.doc`, and `from None` only hides it from the printed traceback. `client.to_epoch_s` and `cli._date_arg` keep `from None`, since their originals hold only the caller's own argument.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. `grep -rn "from None" src/mifit` lists only `client.py` (`to_epoch_s`) and `cli/__init__.py` (`_date_arg`).
- [ ] **Step 5: Commit** `git add tests/support.py src/mifit/transport.py src/mifit/auth/session.py src/mifit/client.py src/mifit/cli/__init__.py tests/test_transport.py tests/test_session_store.py tests/test_client.py tests/test_auth_renew.py tests/test_cli.py && git commit -m "fix: raise scrubbed errors outside except blocks (no __context__ to the original)"`

---

### Task 2: Report a stalled cursor; one shared fake transport (B2, B3, B5)

**Files:**
- Modify:
  - `src/mifit/transport.py` (`PagedResult`, `get_paged_result`, `get_paged` docstring)
  - `src/mifit/domains/base.py` (`stalled_note`)
  - every domain that pages: `steps.py`, `heart_rate.py` (window and full), `heart_rate_daily.py`, `sleep.py` (daily and raw), `workouts.py`, and (M5) `raw_window.py`, `spo2.py`, `spo2_daily.py`, `weight.py`
  - `src/mifit/client.py` (`MiFit.raw`)
  - `tests/support.py`
- Test:
  - `tests/test_pagination.py`, `tests/test_transport.py`, `tests/test_domain_base.py`
  - every domain test file
  - `tests/test_client.py`

**Interfaces:**
- Consumes: `tests/support.exception_chain` (Task 1).
- Produces in `transport.py`:
  - `PagedResult(items: list[dict], truncated: bool, stalled: bool = False)`. `stalled` is `True` exactly when the loop ended on the guard: a page reported a truthy `has_more`, but its `next_key` was missing or empty, or equal to the cursor that request was sent with. It and `truncated` are never both true.
  - `get_paged` is unchanged and stays quiet. Its docstring points to `get_paged_result` for `stalled`.
- Produces in `domains/base.py`: `def stalled_note(what: str) -> str`, returning exactly `f"The service said more {what} followed but sent no usable cursor, so the read stopped early: some {what} in the range may be missing."`.
- Domains: every `t.get_paged(...)` in `src/mifit/domains/` becomes `t.get_paged_result(...)` with the same arguments. When `.stalled` is true, the domain appends `stalled_note(what)` after its row notes, where the workouts cap note goes. The `what` values:

  | Read | `what` |
  |---|---|
  | `steps` | `"daily step rows"` |
  | `heart_rate`, window and full | `"heart-rate rows"` |
  | `heart_rate_daily` | `"daily heart-rate rows"` |
  | `sleep`, daily read | `"daily sleep rows"` |
  | `sleep`, raw read | `"sleep stage rows"` |
  | `workouts` | `"workout rows"` |
  | (M5) `spo2_daily` | `"daily SpO2 rows"` |
  | (M5) `spo2` | `"SpO2 rows"` |
  | (M5) `weight` | `"weight rows"` |

- (M5) `raw_window.fetch_raw_window` returns `RawWindow(NamedTuple)` with fields `kept: list[dict]`, `unusable: int`, `stalled: bool`. Its callers (`heart_rate`, `spo2`, `weight`) unpack all three.
- `MiFit.raw` keeps returning `list[dict]`. It reads through `get_paged_result`, and on `stalled` calls `warnings.warn(stalled_note(f"'{key}' rows"), RuntimeWarning, stacklevel=2)`.
- `tests/support.py` gains:
  - `SYNTH_SSECURITY` (base64 of `b"synthetic-support-secret"`) and `def synthetic_session() -> Session` (all fields synthetic).
  - `class PagedFake(Transport)`, which replaces every per-file `FakeTransport` that served canned rows from `get_paged`:
    - Constructor: `__init__(self, payload=None, *, by_endpoint=None, errors=None, truncated=False, stalled=False, stalled_endpoints=None)`. It needs no session and no HTTP client. `stalled_endpoints` (a set of endpoint paths), when given, narrows `stalled` to those endpoints.
    - `get_paged_result(endpoint_path, data_obj, *, list_key="data_list", sign_path=None, max_pages=None) -> PagedResult`:
      - It records `{"endpoint_path", "data_obj": deepcopy, "list_key", "max_pages"}` in `self.calls`.
      - It raises `errors[endpoint_path]` if that is set.
      - Otherwise it serves a fresh copy of `(payload_for_endpoint.get("result") or {}).get(list_key) or []`, with the configured flags.
      - The inherited `get_paged` reaches it, so either entry point is recorded.
    - `get_json(endpoint_path, data_obj=None, **kwargs)` records `{"endpoint_path", "data_obj"}` in `self.json_calls` and returns that endpoint's payload.
  - `class EncryptingHttp`. Constructor: `__init__(self, payloads: list[dict], *, events: list | None = None, ssecurity: str = SYNTH_SSECURITY)`. Its `get(url, params=None, headers=None, timeout=None)`:
    - appends `("get", None)` to `events`;
    - answers status 200 with the next payload, encrypted for the request's `_nonce` (the last payload repeats);
    - counts its calls in `self.calls`.
- `tests/test_domain_consumption.py` keeps its per-`time` scripted `get_json` fake. It never pages.

- [ ] **Step 1: Write the failing tests:**
  - `test_pagination.py`, using `ScriptedTransport` and `page()`:
    - `test_result_reports_a_stall_when_the_cursor_repeats`: pages `[page(A, has_more=True, next_key="k1"), page(B, has_more=True, next_key="k1")]` give `stalled is True`, `truncated is False`, `items == A + B`, and 2 calls.
    - `test_result_reports_a_stall_when_the_cursor_is_missing`: one page with `has_more=True` and no `next_key` gives `stalled is True` and 1 call.
    - `test_result_not_stalled_when_the_service_ends`: the last page has `has_more=False`, so `stalled is False`.
    - `test_stall_on_the_capped_page_is_not_truncation`: with `max_pages=2`, the second page repeats `"k1"` with `has_more=True`; `stalled is True` and `truncated is False`.
    - `test_paged_result_defaults_to_not_stalled`: `PagedResult([], False).stalled is False`.
  - `test_transport.py::test_every_page_is_paced` (B3):
    - Set up: `EncryptingHttp` with two payloads, `{"code": 0, "result": {"data_list": [{"n": 1}], "has_more": True, "next_key": "k1"}}` and then `{"code": 0, "result": {"data_list": [{"n": 2}], "has_more": False}}`. Patch `transport_module.time.sleep` to append `("sleep", s)` to the same `events` list, and patch `random.uniform` to return 0.0.
    - Call `Transport(synthetic_session(), http=http, delay_s=1.0).get_paged(...)`. It returns `[{"n": 1}, {"n": 2}]`, and `events == [("sleep", 1.0), ("get", None)] * 2`.
  - `test_domain_base.py::test_stalled_note_names_what_may_be_missing`: assert the exact string for `"daily step rows"`.
  - Each paging domain's test file gets `test_stalled_read_says_so`: a fixture served with `PagedFake(..., stalled=True)` ends with exactly one note equal to `stalled_note(<its what>)`. Sleep gets two tests, `test_stalled_daily_read_says_so` and `test_stalled_raw_read_says_so`, using `stalled_endpoints`.
  - `test_client.py`:
    - `test_raw_warns_when_the_read_stalls`: `pytest.warns(RuntimeWarning, match="no usable cursor")`, and the rows are still returned.
    - `test_raw_does_not_warn_on_a_complete_read`: run it under `warnings.catch_warnings()` with `simplefilter("error")`.
- [ ] **Step 2: Run** `python -m pytest tests/test_pagination.py tests/test_transport.py tests/test_domain_base.py -q`. Expected: the new tests FAIL. `test_every_page_is_paced` may already pass, which would confirm B3; keep it as a regression test.
- [ ] **Step 3: Implement** `stalled`, `stalled_note`, the domain switch and `MiFit.raw`. Then migrate the fakes:
  - In `test_client.py`, `test_domain_steps.py`, `test_domain_heart_rate.py`, `test_domain_heart_rate_daily.py`, `test_domain_workouts.py`, `test_domain_sleep.py`, and (M5) `test_domain_spo2_daily.py`, `test_domain_spo2.py`, `test_domain_weight.py`, `test_domain_raw_window.py`: delete the local `FakeTransport` and import `PagedFake` (`from support import PagedFake`; pytest's default rootdir import puts `tests/` on `sys.path`).
  - Assertions that compare whole call dicts gain `"list_key"` and `"max_pages"` (`steps` ~l.114, `heart_rate_daily` ~l.112, `sleep` ~l.141/264, `client` ~l.334).
  - Sleep uses `PagedFake(by_endpoint={DAILY_ENDPOINT: daily, RAW_ENDPOINT: raw}, errors={RAW_ENDPOINT: raw_error} if raw_error else None)`.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes. Then check:
  - `grep -rn "\.get_paged(" src/mifit/domains src/mifit/client.py` prints nothing;
  - `grep -rn "def get_paged(" tests` prints nothing;
  - `grep -rn "class FakeTransport" tests` lists only `test_domain_consumption.py`.
- [ ] **Step 5: Commit** `git commit -m "feat(transport,domains): note a stalled cursor; shared PagedFake test helper"` with the files above.

---

### Task 3: `mifit.chunks`: policy, planning, serial chunked reads, merge

**Files:**
- Create: `src/mifit/chunks.py`, `tests/test_chunks.py`
- Modify: `src/mifit/client.py` (`MiFit.read_chunks`), `tests/support.py` (`WindowFake`)

**Interfaces:**
- Consumes: `REGISTRY`, `DomainResult` (`domains.base`); `PagedFake`, `EncryptingHttp`, `synthetic_session` (Task 2).
- Produces, in `src/mifit/chunks.py`:

```python
ChunkSize = Literal["day", "week", "month"]
CHUNK_DAYS: dict[ChunkSize, int] = {"day": 1, "week": 7, "month": 31}
DOMAIN_CHUNK: dict[str, ChunkSize]   # the table in Global Constraints
OLDEST_FIRST: frozenset[str] = frozenset({"consumption"})

@dataclass(frozen=True)
class Chunk:
    first: date          # local day, inclusive
    last: date           # local day, inclusive
    @property
    def label(self) -> str: ...      # "YYYY-MM-DD..YYYY-MM-DD"

def chunk_size_for(domain: str) -> ChunkSize: ...                     # DOMAIN_CHUNK.get(domain, "week")
def split_days(first: date, last: date, size: ChunkSize) -> list[Chunk]: ...
def plan_chunks(domain: str, first: date, last: date, size: ChunkSize | None = None) -> list[Chunk]: ...
def read_chunks(client: Any, domain: str, start: str, end: str, *, size: ChunkSize | None = None,
                skip: Container[str] = frozenset()) -> Iterator[tuple[Chunk, DomainResult]]: ...
def merge_chunks(parts: Sequence[tuple[Chunk, DomainResult]]) -> DomainResult: ...
```

  - `split_days` returns blocks in ascending order: `[first, first + n − 1]`, then the next `n` days, and so on, with the last block clipped to `last` (`n = CHUNK_DAYS[size]`). Blocks are not aligned to calendar weeks or months, so a range of `n` days or fewer is one chunk. `first > last` or an unknown size raises `ValueError`.
  - `plan_chunks` uses `size or chunk_size_for(domain)` and orders the blocks newest-first, unless `domain in OLDEST_FIRST`. This matches each domain's single-read order, so the merged output equals a single read's.
  - `read_chunks` validates first:
    - `domain` must be in `REGISTRY`, else `ValueError` naming the known domains;
    - `start` and `end` must be `"YYYY-MM-DD"` strings (an int or non-string raises `TypeError` saying chunked reads take dates; a bad date raises `ValueError`);
    - `start <= end`.

    It is a generator: per planned chunk whose label is not in `skip`, it calls `getattr(client, domain)(chunk.first.isoformat(), chunk.last.isoformat())` and yields `(chunk, result)`. Nothing is read before the first `next()`, and each read happens only when the caller asks for it. An exception from a chunk propagates; already yielded chunks are the caller's.
  - `merge_chunks` concatenates `records` and `raw` in the order of `parts`. With more than one part, every note becomes `f"{chunk.label}: {note}"`; with one part, notes are kept as they are. An empty `parts` gives an empty `DomainResult`.
- `MiFit.read_chunks(self, domain: str, start: str, end: str, *, size: ChunkSize | None = None, skip: Container[str] = frozenset())` returns `chunks.read_chunks(self, domain, start, end, size=size, skip=skip)`. Docstring:
  - chunks are whole local days, read one at a time through this client's transport (same pacing, one renewal budget);
  - `skip` takes labels of chunks already done;
  - `merge_chunks` gives one result.
- `tests/support.py: class WindowFake(Transport)`, a model of the service's window rule (notes/06, notes/10): `__init__(self, rows: list[dict])`, and `get_paged_result(...)`:
  - records calls like `PagedFake`;
  - serves the rows with `start < row["time"] <= end`, where `start = data_obj.get("start_time")` (no lower bound when absent) and `end = data_obj["end_time"]`;
  - sorts them by `time` descending, with `truncated` and `stalled` false.

- [ ] **Step 1: Write the failing tests in `tests/test_chunks.py`:**
  - `test_split_days_month_blocks_count_from_the_first_day`: `split_days(date(2026,1,1), date(2026,3,15), "month")` labels are `["2026-01-01..2026-01-31", "2026-02-01..2026-03-03", "2026-03-04..2026-03-15"]`.
  - `test_split_days_week_blocks_cross_the_year`: `(date(2025,12,30), date(2026,1,12), "week")` gives `["2025-12-30..2026-01-05", "2026-01-06..2026-01-12"]`.
  - `test_range_within_one_block_is_one_chunk`: `(date(2026,10,1), date(2026,10,31), "month")` gives `[Chunk(date(2026,10,1), date(2026,10,31))]`. A single day gives one chunk; `"day"` over 3 days gives 3 chunks.
  - `test_split_days_rejects_reversed_range_and_unknown_size`.
  - `test_plan_is_newest_first_except_consumption`: for `steps` the first label is the newest block; for `consumption` it is the oldest.
  - `test_every_registered_domain_has_a_chunk_size`: after `import mifit`, `set(REGISTRY) <= set(DOMAIN_CHUNK)`.
  - `test_raw_point_domains_use_week`: `heart_rate` (and, when registered, `spo2`) map to `"week"`; every other entry maps to `"month"`.
  - `test_merge_concatenates_and_prefixes_notes` and `test_merge_of_one_part_keeps_notes_as_they_are`.
  - `test_read_chunks_matches_single_read`, parametrized over `steps`, `heart_rate_daily`, `heart_rate` and `workouts`:
    - Setup:
      - Patch `client_module.Transport` to return one `WindowFake` and build the client as `MiFit(synthetic_session())`.
      - Rows run from 2025-12-31 to 2026-03-16, so some lie outside the range on both sides:
        - `steps`/`heart_rate_daily`: one daily row per date, keyed at that date's UTC midnight.
        - `heart_rate`: rows at `local_midnight(d) + k * 21600` for k = 0..3, so every block boundary has a row exactly on it.
        - `workouts`: a row at local noon every third day, list key `sport_records`.
    - Assertions:
      - `merge_chunks(list(client.read_chunks(domain, "2026-01-01", "2026-03-15")))` equals `getattr(client, domain)("2026-01-01", "2026-03-15")` in both `records` and `raw`.
      - The chunked read made `len(plan_chunks(domain, …))` calls.
  - `test_read_chunks_skips_done_labels`: with `skip={"2026-03-04..2026-03-15"}`, the steps read yields only the other two chunks.
  - `test_read_chunks_is_lazy`: after creating the generator, `fake.calls == []`; after one `next()`, there is exactly 1 call.
  - `test_read_chunks_rejects_unknown_domain_int_dates_and_reversed_range`.
  - `test_chunks_share_one_paced_transport`:
    - Setup: `events = []`, a patched `transport_module.time.sleep` and `random.uniform → 0.0`, and `MiFit(synthetic_session(), http=EncryptingHttp([{"code": 0, "result": {"data_list": [], "has_more": False}}], events=events), auto_renew=False)`.
    - Reading all chunks of `steps` from 2026-01-01 to 2026-03-15 gives `events == [("sleep", 1.0), ("get", None)] * 3`. That is serial, paced, and one transport.
- [ ] **Step 2: Run** `python -m pytest tests/test_chunks.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `chunks.py`, `WindowFake` and `MiFit.read_chunks`. `client.py` imports `chunks`; `chunks` must not import `client` (no cycle). The `chunks` module docstring states:
  - the per-domain sizes and why: one page per daily-report chunk at `limit` 50, a 1-day margin per raw chunk, 31 one-day requests per consumption chunk;
  - that blocks count from the first day;
  - the order rule;
  - that no extra delay is added because the transport paces every request.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/chunks.py src/mifit/client.py tests/support.py tests/test_chunks.py && git commit -m "feat(chunks): chunk policy, serial chunked reads and merge (MiFit.read_chunks)"`

---

### Task 4: Strict JSON output (B1)

**Files:**
- Modify: `src/mifit/cli/__init__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces in `cli/__init__.py`:
  - `def _finite(value: Any) -> tuple[Any, int]` returns a copy in which every non-finite `float` (NaN, +Infinity, −Infinity), at any depth of dicts and lists or tuples, is `None`, along with the number replaced. Lists and tuples become lists; `bool` and `int` are untouched.
  - `def _json_ready(result: DomainResult) -> DomainResult` builds `records` via `_plain` and runs both `records` and `raw` through `_finite`. When anything was replaced, it appends the note `f"Replaced {n} non-finite number(s) (NaN or Infinity) with null: strict JSON has no such values."`.
- Export writes `json.dumps({...}, indent=2, allow_nan=False)` built from `_json_ready(result)`. Notes are printed to stderr after `_json_ready`, so the replacement note is printed too.
- (M5) If `mifit profile` shares export's output path, it uses `_json_ready` as well.

- [ ] **Step 1: Write the failing tests:**
  - `test_export_replaces_non_finite_numbers_with_null`:
    - Setup: patch `FakeMiFit.steps` to return `raw=[{"a": float("nan"), "b": [float("inf"), float("-inf"), 1.5]}]`, and run with `--json` to a file.
    - Assert:
      - `json.loads(text, parse_constant=_reject)` succeeds, where `_reject` calls `pytest.fail`;
      - `raw == [{"a": None, "b": [None, None, 1.5]}]`;
      - `notes[-1] == "Replaced 3 non-finite number(s) (NaN or Infinity) with null: strict JSON has no such values."`;
      - stderr contains that note.
  - `test_export_non_finite_record_field_becomes_null`: a `ConsumptionRecord` with `resting_kcal=float("nan")` gives `records[0]["resting_kcal"] is None`.
  - `test_export_stdout_is_strict_json`: the same NaN, written to stdout, parses with `_reject`.
  - `test_export_finite_output_has_no_replacement_note`: the plain steps export's `notes == []`.
  - (M5) `test_profile_output_is_strict_json`, if `mifit profile` exists.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli.py -q`. Expected: the NaN tests FAIL.
- [ ] **Step 3: Implement** `_finite`, `_json_ready` and `allow_nan=False`.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git commit -m "fix(cli): strict JSON output (NaN/Infinity -> null, with a note)"`

---

### Task 5: Partial-export store (`mifit.cli.partial`)

**Files:**
- Create: `src/mifit/cli/partial.py`, `tests/test_cli_partial.py`

**Interfaces:**
- Produces, in `src/mifit/cli/partial.py`:

```python
FORMAT = 1
class PartialError(ValueError): ...   # damaged, or belongs to another export; never quotes file content

def partial_path(json_path: str | Path) -> Path: ...                     # Path(f"{json_path}.partial")
def export_header(domain: str, start: str, end: str, chunk: str) -> dict: ...
    # {"mifit_partial": FORMAT, "domain": domain, "from": start, "to": end, "chunk": chunk}
def start_partial(path: Path, header: dict) -> None: ...                 # create/truncate; header line
def append_part(path: Path, label: str, part: DomainResult) -> None: ...
    # one line {"chunk": label, "records", "raw", "notes"}
def load_partial(path: Path, header: dict, labels: Collection[str]) -> dict[str, DomainResult]: ...
```

  - **The format is JSON Lines**, UTF-8, LF. Every line is `json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`. `start_partial` and `append_part` open the file, write one line, then `flush()` and `os.fsync()`.
  - **`append_part`** takes a part that is already `_json_ready` (plain records). It raises `ValueError` on a non-finite float, which strict JSON enforces.
  - **`load_partial`** checks the file:
    - An empty file, or a first line that is not exactly `header`, raises `PartialError`. For a header of another export, the message names the stored `domain`, `from`, `to` and `chunk` and says to rerun with those arguments or delete the file.
    - The **last** line, if it does not parse or lacks a field, is dropped: that chunk is simply read again.
    - Any other bad line, or a label not in `labels`, raises `PartialError("… is damaged; delete it to start over")`.
    - It returns `label → DomainResult(records, raw, notes)`. A later line for the same label wins.
  - A `PartialError` is raised after the `except` block, as in Task 1: no `__context__`, because a `JSONDecodeError` keeps the line in `.doc`.

- [ ] **Step 1: Write the failing tests:**
  - `test_partial_path_appends_suffix`: `partial_path("out.json") == Path("out.json.partial")`.
  - `test_start_then_load_returns_no_parts`.
  - `test_append_then_load_round_trips_parts`: two parts with nested records and notes come back equal.
  - `test_every_line_is_strict_json_and_lf`: each line parses with a rejecting `parse_constant`, and the file bytes contain no `b"\r"`.
  - `test_append_rejects_non_finite_numbers`: a part with `raw=[{"x": float("nan")}]` raises `ValueError`.
  - `test_load_drops_a_torn_last_line`: append two parts, then cut the last 5 bytes off. `load_partial` returns only the first part.
  - `test_load_rejects_a_damaged_middle_line`: insert `"{not json\n"` after the header, then append one good part. This raises `PartialError` whose message contains `"damaged"` and not `"not json"`. Its chain, `support.exception_chain`, is just itself.
  - `test_load_rejects_another_exports_header`: written with `to="2026-03-15"` and loaded with `"2026-03-20"`, the `PartialError` message contains `"2026-03-15"` and `"--resume"`.
  - `test_load_rejects_unknown_chunk_label` and `test_load_rejects_a_file_without_header`.
  - `test_header_holds_only_export_arguments`: `set(export_header(...)) == {"mifit_partial", "domain", "from", "to", "chunk"}`.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli_partial.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `partial.py`.
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes.
- [ ] **Step 5: Commit** `git add src/mifit/cli/partial.py tests/test_cli_partial.py && git commit -m "feat(cli): partial-export store (JSON Lines, fsync per chunk, torn-tail tolerant)"`

---

### Task 6: Chunked `mifit export`: opt-in, progress, resume, failure handling (B6); README and roadmap

**Files:**
- Modify: `src/mifit/cli/__init__.py`, `tests/test_cli.py`, `README.md`, `docs/superpowers/roadmap.md`, `.gitignore` (add `*.partial`)

**Interfaces:**
- Consumes:
  - `plan_chunks`, `chunk_size_for`, `CHUNK_DAYS`, `merge_chunks`, `Chunk` (Task 3);
  - `_json_ready` (Task 4);
  - `partial_path`, `export_header`, `start_partial`, `append_part`, `load_partial`, `PartialError` (Task 5).
- New `export` flags:
  - `--long`: "allow a range over 92 days: read in chunks, one request at a time; can take many minutes".
  - `--chunk {day,week,month}`: "chunk size for a range longer than one chunk (default: per domain)".
  - `--resume`: "continue an interrupted export from PATH.partial (needs --json)".
- `_LONG_RANGE_DAYS = 92`.
- Usage checks in `main`, after parsing, next to the existing `--from > --to` check. Each is `parser.error`, so it exits 2 and sends no request:
  - `--resume` without `--json`;
  - `heart_rate --full` together with any of `--long`, `--chunk` or `--resume` (the full read is one backward read and cannot be chunked);
  - a span over 92 days without `--long`. `heart_rate --full` is exempt. The message names the span, the 92-day limit and `--long`.
- `_cmd_export` flow:
  1. **Keep** the existing "--full ignored" note.
  2. **Plan.** `heart_rate --full` gets `plan = [Chunk(first, last)]` with `full=True`. Every other export gets `plan = plan_chunks(domain, first, last, args.chunk)`, and `size = args.chunk or chunk_size_for(domain)`. The export is **chunked** iff `len(plan) > 1`.
  3. **Partial file.**
     - It is used only when the export is chunked and `--json` is given: `partial = partial_path(args.json_path)`.
     - A partial that exists without `--resume` is an error, exit 1, before the client opens and with the file left untouched: `error: <partial> holds an unfinished export; rerun with --resume to continue it, or delete it to start over.`
     - `--resume` with a partial loads it with `export_header(domain, start, end, size)` and the plan's labels; a `PartialError` is reported as `error: …`, exit 1. Then it prints `resuming <partial>: <k> of <n> chunk(s) already done`.
     - `--resume` without one prints `nothing to resume at <partial>; starting from the first chunk`.
     - When the export is not chunked, `--resume` is a no-op.
  4. **Announce.** When chunked, print `export: <n> chunks of up to <CHUNK_DAYS[size]> days, read one at a time.` to stderr.
  5. **Open** one client for the whole export (`_open_client()`).
  6. **Read** the plan's chunks in order, skipping done ones. Each one calls `_fetch(client, domain, chunk.first.isoformat(), chunk.last.isoformat(), full)` and then `_json_ready`. When a partial is in use, the result is appended, and the partial is created with `start_partial` just before the **first** append, so a run that completes no chunk leaves no file. When chunked, print `chunk <i>/<n> <label>: <r> record(s)` to stderr.
  7. **Merge** in plan order with `merge_chunks([(c, parts[c.label]) for c in plan])`. Print the notes, then write the output as in Task 4. After a successful write to `--json`, delete the partial.
- Failure inside step 5 or 6:
  - The existing handlers stay as they are: `AuthError` with the session line and "Run `mifit login`"; `MiFitError`; `requests.RequestException` reported by type only.
  - When chunked, the error line adds ` (chunk <i>/<n> <label>)` for the chunk that failed.
  - When the partial exists, a final line says `Completed chunks are saved in <partial>; rerun the same command with --resume to continue.`, with "after `mifit login`, " before "rerun" for an `AuthError`.
  - `KeyboardInterrupt`: print the same resume line when the partial exists, then re-raise, so `main` prints `Interrupted.` and returns 130.
  - The output file is not written after any failure.
- The single-chunk path calls `_fetch(client, domain, args.start, args.end, full)` with exactly today's arguments: a plan of one chunk has `first..last` equal to `--from..--to`.

- [ ] **Step 1: Write the failing tests in `test_cli.py`.**

  Helper: `echo(fail_at=None, error=None)` returns a `steps(self, start, end)` that records `("steps", start, end, {})`. It raises `error` when `start == fail_at`. Otherwise it returns `DomainResult(records=[{"chunk": f"{start}..{end}"}], raw=[{"from": start}], notes=[f"read {start}"])`. Install it with `monkeypatch.setattr(FakeMiFit, "steps", echo(...))`; there is one for consumption as well.

  Tests:
  - `test_short_range_is_one_call_as_before`: `steps 2026-10-01..2026-10-02 --json out`. The calls are `[("steps", "2026-10-01", "2026-10-02", {})]`, no `out.json.partial` exists at any point, and stderr has no `"chunk"`.
  - `test_multi_chunk_export_reads_each_chunk_newest_first`: `steps --from 2026-01-01 --to 2026-03-15 --json out`.
    - The calls are, in order, `[("steps", "2026-03-04", "2026-03-15", {}), ("steps", "2026-02-01", "2026-03-03", {}), ("steps", "2026-01-01", "2026-01-31", {})]`.
    - Records are the three echoes in that order.
    - `notes[0] == "2026-03-04..2026-03-15: read 2026-03-04"`.
    - Stderr has `"chunk 1/3"` and `"3 chunks of up to 31 days"`.
    - The partial is gone.
  - `test_consumption_chunks_run_oldest_first`: the same range gives calls starting `("consumption", "2026-01-01", "2026-01-31", {})`.
  - `test_chunk_flag_overrides_domain_default`: `steps 2026-10-01..2026-10-14 --chunk week` gives the calls `[("steps", "2026-10-08", "2026-10-14", {}), ("steps", "2026-10-01", "2026-10-07", {})]`.
  - `test_long_range_needs_long_flag`: `2026-01-01..2026-04-03` (93 days) exits 2, stderr has `"--long"` and `"92"`, and `FakeMiFit.instances == []`.
  - `test_92_days_need_no_flag`: `..2026-04-02` exits 0.
  - `test_long_flag_runs_a_long_range`: 93 days with `--long` exits 0 with 3 calls.
  - `test_failed_chunk_keeps_completed_chunks_and_points_to_resume`:
    - Setup: `echo(fail_at="2026-02-01", error=ApiError("boom"))` over `2026-01-01..2026-03-15 --json out`.
    - Exit 1. Stderr has `"(chunk 2/3 2026-02-01..2026-03-03)"` and `"--resume"`.
    - `out.json` does not exist.
    - The partial's lines are the header `{"mifit_partial": 1, "domain": "steps", "from": "2026-01-01", "to": "2026-03-15", "chunk": "month"}` and one part labeled `"2026-03-04..2026-03-15"`.
  - `test_resume_reads_only_remaining_chunks`:
    - After the failure above, rerun with `--resume` and a plain `echo()`.
    - The new instance's calls are only the `2026-02-01` and `2026-01-01` chunks.
    - `out.json` equals the output of an uninterrupted run (made in another tmp dir).
    - The partial is gone.
  - `test_resume_with_every_chunk_done_sends_no_request`: a partial holding all 3 parts with `--resume` gives zero calls, and `out.json` is written.
  - `test_existing_partial_without_resume_is_refused`: exit 1, stderr has `"--resume"`, no instance is created, and the partial is byte-identical.
  - `test_resume_with_different_arguments_is_refused`: the partial is from `--to 2026-03-15`; a rerun with `--to 2026-03-20 --resume` exits 1, stderr has `"2026-03-15"`, and the partial is untouched.
  - `test_resume_without_partial_starts_fresh`: exit 0, and stderr has `"nothing to resume"`.
  - `test_resume_needs_json`: exit 2.
  - `test_full_cannot_be_combined_with_chunking_flags`: parametrized over `--long`, `--chunk week` and `--resume --json x`; `heart_rate --full` with each exits 2.
  - `test_full_heart_rate_long_range_is_one_call`: `heart_rate --full --from 2026-01-01 --to 2026-07-31` exits 0 with one call carrying `{"full": True}` and the given dates.
  - `test_auth_error_mid_export_keeps_progress`: an `AuthError` at the second chunk exits 1. Stderr has `"mifit login"`, `"--resume"` and a `session:` line, and the partial holds one part.
  - `test_ctrl_c_mid_export_keeps_progress`: a `KeyboardInterrupt` at the second chunk returns 130. Stderr has `"Interrupted."` and `"--resume"`, and the partial holds one part.
  - `test_partial_holds_no_session_secret`: after the failure test's setup, the partial's text contains neither `SYNTH_TOKEN` nor `SYNTH_SSECURITY`, and its header keys are the five export fields.
  - `test_chunked_export_to_stdout_has_no_partial`: a multi-chunk export with no `--json` prints the merged JSON on stdout, and `list(tmp_path.glob("*.partial")) == []`. Run it with `monkeypatch.chdir(tmp_path)`.
  - The existing export tests stay unchanged and pass: their ranges fit in one chunk.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the flags, the checks and the flow. Keep `_cmd_export` readable by splitting out small helpers: `_plan(args) -> tuple[list[Chunk], str, bool]`, `_load_progress(...)` and `_resume_hint(partial, auth: bool) -> str`. Update the module docstring: chunking, `--long`, `--resume`, and that the partial holds only the export's own data.
- [ ] **Step 4: Update `README.md`:**
  - **Export section.** Show the synopsis `mifit export <domain> --from … --to … [--json PATH] [--full] [--chunk day|week|month] [--long] [--resume]` and explain:
    - a range longer than one chunk (7 days for raw heart rate and SpO2, 31 days for everything else) is read chunk by chunk with progress on stderr;
    - ranges over 92 days need `--long`;
    - with `--json`, progress is kept in `PATH.partial` (your exported data plus the export's arguments, no credentials) and an interrupted export continues with `--resume`;
    - a chunk that keeps failing can be skipped by exporting the ranges around it.
  - **`<domain>` list.** Bring it up to date with `REGISTRY`.
  - **Python API.** Add `for chunk, part in client.read_chunks("heart_rate", "2026-01-01", "2026-06-30"): ...` and `from mifit.chunks import merge_chunks`.
  - **Known limitations.** Replace the "Be gentle" bullet's "long ranges are not split up yet, so export a few weeks at a time" with: requests go one at a time with about a second's pause plus jitter, long ranges are chunked and need `--long`, and this lowers but does not remove the risk of rate limiting.
  - **Two more limitations.** Say that NaN and Infinity in service data become `null` with a note, and that a read the service breaks off without a cursor says so in `notes`.
- [ ] **Step 5: Update `docs/superpowers/roadmap.md`:**
  - In §0 "横向工作", replace T3's "何时做" cell with `✅ 实现完成（2026-10-14）：分块串行读取、超过 92 天需 --long、--resume 断点续传`.
  - In §1, update the test count to the number `python -m pytest -q` prints.
  - Under §4 "T3 长区间 / 全量导出", add:
    > **结果（2026-10-14）✅**：`mifit.chunks` 按数据域分块（原始心率 / 血氧 7 天，其余 31 天，从 `--from` 起算），逐块串行读取，每个请求前仍是传输层的约 1 s + 抖动；一块装得下的区间仍是一次调用，与以前相同。超过 92 天需 `--long` 显式开启；`heart_rate --full` 不分块。`--json` 旁的 `<PATH>.partial` 每完成一块追加一行（只有数据域、日期、块大小和该块结果，不含凭证），中断后 `--resume` 从未完成的块继续；某块失败时已完成的块保留，不跳过也不自动重试。另：服务端说还有下一页却不给可用游标时结果带提示；CLI 输出为严格 JSON（NaN/Infinity 记为 null 并提示）。库接口 `MiFit.read_chunks` + `merge_chunks`。
- [ ] **Step 6: Run** `python -m pytest -q`. Expected: everything passes. Then `python -m pip install -e . && mifit export --help` lists `--long`, `--chunk` and `--resume`.
- [ ] **Step 7: Commit:**
  - First `git add src/mifit/cli/__init__.py tests/test_cli.py README.md .gitignore && git commit -m "feat(cli): chunked long-range export with --long opt-in and --resume"`.
  - Then `git add docs/superpowers/roadmap.md && git commit -m "docs(roadmap): T3 implemented"`.

---

### Task 7: Shared row decoding and positive-float helper (cleanup added by controller ruling)

**Why:** reviews of M3–M5 found the same "decode a row's `value` JSON, note when it is missing / not valid JSON / not an object" block copied in `heart_rate`, `heart_rate_daily`, `steps`, `sleep` (daily and raw reads), `spo2`, `spo2_daily`, `workouts` and `weight`, and the "positive float or None" idiom repeated in `weight` (BMI) and `profile`. `sleep.py` also keeps its own copy of the raw endpoint string.

**Files:**
- Modify: `src/mifit/domains/base.py`, the domain modules above, `src/mifit/domains/profile.py`, `tests/test_domain_base.py`
- Do not modify existing domain tests except to import shared helpers; every existing domain test must pass unchanged.

**Requirements:**
- [ ] Add to `domains/base.py` one helper that decodes a row's `value` into a `dict`, or reports why it could not (missing, not valid JSON, not an object), so each domain can keep its exact current note text (the helper must not change any note wording, note order, or what goes into `raw`). Unit-test it directly (absent key, `None`, non-string, invalid JSON, a JSON array, a JSON object, a 5000-digit integer literal).
- [ ] Add `as_positive_float(raw) -> float | None` (`as_float`, then `None` unless `> 0`) with direct unit tests; use it for weight's BMI/weight check and profile's `_positive` (remove the local copies).
- [ ] Switch every listed domain to the shared decoder. Behavior must be byte-identical: same records, same `raw`, same notes in the same order — the existing domain tests are the proof and stay unchanged.
- [ ] `sleep.py`'s raw endpoint constant aliases `raw_window.RAW_ENDPOINT`.
- [ ] Run `python -m pytest -q`; commit `refactor(domains): shared row decoding and as_positive_float`.

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Chunking lives in the library; persistence lives in the CLI.** `mifit.chunks` holds the policy, splitting, a lazy serial `read_chunks` and `merge_chunks`, and `MiFit.read_chunks` exposes them to SDK users. The `.partial` format, `--resume`, `--long` and progress lines are CLI choices: SDK users own their storage and resume with `skip`. The CLI loops over `plan_chunks` itself rather than consuming `read_chunks`, so it always knows which chunk is in flight for its messages. The loop is three lines, and the policy and merge are shared.
2. **Blocks are fixed lengths counted from `--from`** (day 1, week 7, month 31 days), not calendar-aligned. A range that fits in one block is exactly today's single call, which honors "keep single-request behavior for short ranges". Calendar alignment would split, for example, a 3-day Sat–Mon heart-rate range into two reads, each with its own 1-day margin. Labels name both ends, so nothing is ambiguous.
3. **Sizes per domain:**
   - Daily-report domains get `month`: 31 rows fit one page at `limit` 50.
   - Consumption gets `month`: one request per day either way, and a resume step of about 31 requests.
   - Workouts and weight get `month`: they are sparse.
   - Raw heart rate and SpO2 get `week`: 144 to 1440 rows a day, and the 1-day margin costs 1/7 extra instead of 100% with day chunks.
   - An unknown future domain gets `week` (conservative), and `test_every_registered_domain_has_a_chunk_size` forces an explicit entry.
   - `--chunk` overrides the size.
4. **Order:** chunks run newest-first, matching the services' `reverse=True` reads, except consumption, whose single read walks days oldest-first. So a merged chunked export equals the single read's `records`, and Task 3 proves this on a window-rule fake. Merged `raw` keeps chunk order; for sleep that interleaves daily values and matched raw sessions per chunk, which is the same content in a different order.
5. **Merge:** `records` and `raw` are concatenated in plan order. Notes get a `"<first>..<last>: "` prefix only when there is more than one chunk, so single-chunk output is unchanged and a repeated note, such as a cap or stall note, says which chunk it came from.
6. **Explicit opt-in is a CLI rule:** a span over 92 days needs `--long`. Ninety-two days is about a quarter. At that size the heavy domains run hundreds of requests (raw HR at about 5 pages a week; consumption at 1 request a day), which is the "全量/长区间" the design wants switched on deliberately. Shorter multi-chunk ranges are chunked by default, without a flag. Library calls are explicit by nature and are not guarded.
7. **`--full` stays one backward read** (at most 10 pages, with the existing cap note). It cannot be chunked, so `heart_rate --full` rejects `--long`, `--chunk` and `--resume` (exit 2) and is exempt from the 92-day rule, since it ignores `--from`.
8. **Resume works through `<PATH>.partial`:**
   - **Format.** JSON Lines: a header line `{mifit_partial, domain, from, to, chunk}`, then one line per completed chunk. Each line is flushed and fsync'd.
   - **Lifetime.** The file is created at the first completed chunk and deleted after the final write.
   - **Explicit resume.** Resuming needs `--resume`. An existing partial without it is refused, so throttled hours of progress are never clobbered, and a mismatched header is refused with the stored arguments named.
   - **Torn tail.** A torn last line is dropped and that chunk re-read.
   - **Contents.** It holds no secrets, only the export's own data (as sensitive as the output itself) and its arguments; `.gitignore` gets `*.partial`.
   - **Stdout.** An export to stdout is chunked but not resumable.
9. **B6: a failed chunk stops the export and keeps every completed chunk.** There is no skip-and-continue, because silently missing chunks would read as complete data. There is no automatic retry either: a failure may be throttling, and retrying is the wrong reflex for design §3. `--resume` retries from the failed chunk. Inside one chunk, the domains' existing rules stand: consumption skips a day on `ApiError` with a note, and sleep's raw-read `ApiError` keeps the days without stages. A network error anywhere fails that chunk, which the partial makes cheap. The output file is never written after a failure.
10. **B3, pacing: already in place; verified, not changed.** `Transport._send` pauses `delay_s` (1.0) plus `U(0, 0.5·delay_s)` before every request: each page of a paged read (via `get_json`) and the post-renewal retry. Task 2 pins this for pages, and Task 3 for a whole chunked read through one transport. No extra inter-chunk pause is added: the transport's pause already separates the last request of one chunk from the first of the next. The renewal's own two account-service calls are unpaced, as in T2; they happen at most 3 times per client.
11. **Session renewal:** one `MiFit` per CLI run, so the transport's budget (1 renewal per request, 3 per transport) covers the whole export. A 401 renews and continues. A failed renewal stops with "mifit login, then --resume", and the rerun gets a fresh budget.
12. **B2, a stalled cursor:** `PagedResult.stalled` (default `False`, so positional `PagedResult(items, truncated)` still works). Every paging domain adds `stalled_note(what)`, mirroring the existing cap notes. `get_paged` stays quiet for compatibility, and `MiFit.raw` (which returns a bare list) warns with a `RuntimeWarning`, as the client already does for a failed session save.
13. **B1, strict JSON:** the CLI replaces non-finite floats with `null`, counts them in a note, and writes with `allow_nan=False`. The library keeps Python floats untouched, since NaN is legal in Python. Partial lines are strict too.
14. **B4, exception chains:** the raise-after-block pattern goes into `transport.get_json` (decrypt), `session.load_session` (`JSONDecodeError.doc` is the whole file, credentials included), `MiFit.__init__` and `cli._open_client`, each with a chain-walking test. `client.to_epoch_s` and `cli._date_arg` keep `from None`: their originals hold only the caller's own date argument.
15. **B5, shared fakes: done, as part of B2.** Moving domains to `get_paged_result` breaks every fake that overrides only `get_paged`, so they are replaced by one `tests/support.PagedFake` instead of editing 7–11 copies. `WindowFake`, `EncryptingHttp`, `synthetic_session` and `exception_chain` join it. Consumption's per-day scripted fake stays: it models a different endpoint.
16. **No live task.** The change is client-side: windows the service already answers correctly, split differently. An optional end-of-run smoke (a 2-month `steps` export chunked with `--chunk week`, compared with the single read) goes onto the program ledger's end-of-run list; the controller adds it.
17. **M5 coupling:** the plan targets `main` after M5. M5's domains get table entries and stall notes, and `raw_window.fetch_raw_window` returns a `RawWindow` named tuple with a `stalled` field. Rows marked (M5) are skipped for any file that does not exist.

## Self-Review

1. **Spec coverage:**
   - Roadmap T3 "默认分块（按周或按月）": Tasks 3 and 6, Decision 3.
   - "串行 + 带抖动的延时": Decision 10, with pacing pinned in Tasks 2 and 3.
   - "全量导出需要显式开启": `--long`, Task 6, Decision 6.
   - "断点续传": Tasks 5 and 6.
   - "`--full` 等分页上限触顶时给出明确提示": existing cap notes, plus the stall note (Task 2), with chunk-labeled notes (Decision 5).
   - Design §3, serial with no concurrency: one client, a lazy generator, no threads.
   - Design §3, `limit` ~50: unchanged.
   - Design §2, the account-risk anti-goal: opt-in, pacing and no auto-retry.
   - B1–B6: Tasks 4, 2, 2/3, 1, 2 and 6, with a verdict in Decisions 9–15. ✓
2. **Step scan:**
   - Every code step names its file, signature and pinned values: chunk days, the 92-day limit, note texts, the header fields, stderr strings.
   - The CLI flow is written out once, in Task 6's Interfaces, because the order of partial creation, client opening and messages is exactly what the tests check.
   - No function body is given where the signature and tests already determine it. ✓
3. **Type consistency:** these names are used identically everywhere: `Chunk(first, last).label`, `ChunkSize`, `CHUNK_DAYS`, `DOMAIN_CHUNK`, `OLDEST_FIRST`, `chunk_size_for`, `split_days`, `plan_chunks`, `read_chunks`, `merge_chunks`, `PagedResult(items, truncated, stalled=False)`, `stalled_note(what)`, `RawWindow(kept, unusable, stalled)`, `_finite`, `_json_ready`, `partial_path`, `export_header`, `start_partial`, `append_part`, `load_partial`, `PartialError`, `PagedFake`, `WindowFake`, `EncryptingHttp`, `synthetic_session` and `exception_chain`. The header keys are `mifit_partial`, `domain`, `from`, `to` and `chunk` in Tasks 5 and 6. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 3, 4, 5 or 6. ✓
5. **Proportion:** six tasks. The only code block is the `chunks`/`partial` interface. Tests are named with their pinned values; bodies are left to the implementer. ✓
