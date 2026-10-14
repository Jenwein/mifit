# mifit Final Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 11-item final-polish backlog: one shared daily-report reader, clean errors instead of tracebacks for data nested too deeply, shared test helpers and two more guard tests, and wording fixes in the docs. No domain's output changes.

**Architecture:** A new `domains/daily_report.py` holds two things: the request every daily-report read sends (`read_daily_report`) and the row loop of the three per-day summary domains (`fetch_daily_summaries`). `heart_rate_daily`, `spo2_daily` and `stress_daily` shrink to one call plus a field map; `steps` and `passthrough.fetch_daily_rows` reuse the request only. `RecursionError` joins the exceptions that the CLI's output writer, its chunk saver and the transport's body decoder already turn into their existing clean errors. Test-only duplication moves into `tests/support.py`. The docs changes are wording only.

**Tech Stack:** Python 3.12+ (CI runs 3.12, 3.13 and 3.14), pytest, existing mifit modules. No new dependencies.

**Spec:** the controller's backlog `.superpowers/drafts/polish-backlog.md` (git-ignored, so it is restated below) and the review rulings each item came from, all in `.superpowers/program-ledger.md`:

1. **Shared daily-report row reader.** `heart_rate_daily`, `spo2_daily` and `stress_daily` repeat the same loop: `decode_value` plus a per-domain `_VALUE_NOTES`, raw appended before the day check, `as_int` day key → `report_day`/`local_midnight` with `(ValueError, OverflowError, OSError)`, and the stalled note. They also repeat the same request dict (`tag=daily_report`, key, `reverse`, `limit 50`, `report_window(*local_day_span)`), and `passthrough.fetch_daily_rows` repeats the dict too. Extract one helper with byte-identical behavior: every existing domain test passes unchanged. `steps` may join only if byte-identical. *(Ledger [M7] Task 3; [M3]–[T3] backlog "shared daily-report row decoder".)*
2. **RecursionError.** On Python 3.12/3.13, `json.dumps(indent=2)` in the CLI's `_write_result` raises `RecursionError` for data nested about 1000–2000 levels deep, data that `json.loads` accepted. Catch it around the dump and print a clean error (exit 1), with the resume hint where applicable. The transport's decrypt path (the `decrypt_response_payload` catch) also lets `RecursionError` escape; treat it as the existing undecryptable-body `ApiError`. *(Ledger [T3] Final parked item and the backlog line before it.)*
3. **Shared `payload_with_rows`.** Move the seven per-file copies in `tests/` to `tests/support.py`; the tests' behavior is unchanged. *(Ledger [M4] backlog "(7 copies)" and the T3 backlog line.)*
4. **`DomainResult.raw` annotation:** `list[dict]` → `list[Any]` (`fetch_daily_rows` keeps rows that are not objects), with one docstring sentence. *(Ledger [M7] Final.)*
5. **Catalog reverse guard** in `tests/test_domain_catalog.py`: every backticked B-layer name in `notes/domain-catalog.md` §2/§3 is registered in `REGISTRY` or is `profile`. *(Ledger [M7] Final.)*
6. **Sleep chunk layout pin** in `tests/test_chunks.py`: each part's `raw` is a consecutive slice of `merged.raw`, in plan order. *(Ledger [T3] Task 3, ruling I1.)*
7. **T2 wording.** `lifetime.py` (lines 9–10), `notes/09` (line 91) and `tests/test_transport.py` (the comment, now near line 581) say `renew_network_error` means "the passToken was never judged". An STS-only failure can follow an accepted serviceLogin, so reword it: "the renewal did not complete because of a network failure". Also note that `renew_failed` is an upper bound on refusals. *(Ledger [T2] Final.)*
8. **README.** (a) "any key of the fitness-data endpoint" → "…of the fitness-data or (with `--daily`) daily-report endpoint", wherever `mifit raw` / `daily_report` are described. (b) In "Two layers": for `raw_window`, `daily_report` and `mifit raw`, `raw` is the whole outer rows with `value` decoded. (c) The `MiFit.raw` caveat (first-bucket drop) applies to every bucketed raw key, `spo2` and `stress` included (in the catalog, every dense raw key checked drops it); point to `raw_window`. (d) Mention that `read_chunks` refuses `options={"full": True}`. *(Ledger [M7] Final; [T1] Final parked README wording.)*
9. **Roadmap.** The M4 line "0 或缺失记为 None" should note that a `utc_offset` of 0 is kept. In the §6 M7 row, add 活动卡路里 (active calories) to the app comparison list. Touch nothing else. *(Ledger [M4] Final; [M7] Final.)*
10. **Catalog** (lines 182 and 199): reword the weight account facts as buckets or booleans (e.g. "1–10 条记录；身体成分字段全为 0：是"). *(Ledger [M7] Final.)*
11. **M7 plan Decision 8** (`docs/superpowers/plans/2026-10-14-mifit-domain-catalog.md`): correct it to match the catalog. `vitality`/`training_load`/`vo2_max`/`running_ability_index`/`grade_prediction` are listed in §5, not as 候选 rows. *(Ledger [M7] Final.)*

**Precondition:** branch `final-polish` is checked out, the tree is clean, and HEAD is `97a7e68` plus this plan's commit. Baseline: `python -m pytest -q` → 1178 passed, 2 skipped (the two Windows `tzset` skips).

## Global Constraints

- **Offline tests only.** `tests/conftest.py` blocks the network and isolates `HOME`. No live call anywhere in this plan.
- **Green after every task:** `python -m pytest -q` passes, with exactly the 2 baseline skips.
- **Behavior-preserving refactors** (Task 1, and the helper move in Task 3): the existing domain tests stay unchanged and green. Task 1 only adds test lines. Task 3 replaces only the helper definitions, never a test function's body. New tests are additions.
- **Public API unchanged:** domain names, every `fetch_*` and `MiFit` signature, every note text, and `from mifit.domains.passthrough import AGGREGATED_ENDPOINT`.
- **Public repo:** no account figures, dates, ids or tokens in code, tests, docs or commits. Test values are synthetic.
- **Language per file:** README, docstrings and comments are in English; `notes/`, the roadmap and the catalog are in Chinese.
- **Line endings:** every file is LF. Python's `write_text` on this machine writes CRLF, so after any scripted edit run `git ls-files --eol <file>` and normalize.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. One commit per task, on `final-polish`. Do not push.

## Review Focus

- **The summary domains' note texts after the refactor.** `heart_rate_daily` and `spo2_daily` pin only the stalled note today, so a typo in `what` would go unnoticed. → Task 1 Step 1 adds `test_skip_notes_keep_their_wording` to both files before anything moves.
- **A daily row whose day key is a number out of the date range** (`time` = `10**20` or `-(10**20)`): it is skipped with the day note, its value is still kept in `raw`, and nothing crashes. → Task 1 `test_summaries_skip_a_day_key_out_of_range`.
- **An export of deeply nested data on Python 3.12/3.13**, to stdout, to a file, or chunked with a partial file: one `error:` line, exit 1, no traceback, nothing half-written, and the partial file kept with the resume hint. → Task 2's four CLI tests.
- **A response body nested too deeply to parse:** the fixed-text `ApiError` with no exception chain, never a `RecursionError`. → Task 2 `test_too_deeply_nested_body_is_an_api_error`.
- **A catalog B-layer cell naming a domain that does not exist** (a typo, or a domain that was removed): the guard fails and names it. → Task 3 `test_every_b_layer_name_in_the_catalog_is_a_domain`, plus its bite check.

---

### Task 1: Shared daily-report reader (item 1)

**Files:**
- Create: `src/mifit/domains/daily_report.py`, `tests/test_domain_daily_report.py`
- Modify: `src/mifit/domains/heart_rate_daily.py`, `src/mifit/domains/spo2_daily.py`, `src/mifit/domains/stress_daily.py` (the body of `fetch_*`; `_to_record` → `_summary`; imports), `src/mifit/domains/steps.py` (the request only), `src/mifit/domains/passthrough.py` (the `fetch_daily_rows` request and the `AGGREGATED_ENDPOINT` import), `tests/test_domain_heart_rate_daily.py` and `tests/test_domain_spo2_daily.py` (one added test each)

**Interfaces:**
- Consumes: `Transport.get_paged_result(endpoint_path, data_obj, *, list_key="data_list", ...) -> PagedResult(items, truncated, stalled)`; `days.local_day_span`, `report_window`, `report_day`, `local_midnight`; `base.decode_value`, `ValueProblem`, `as_int`, `stalled_note`, `DomainResult`.
- Produces in `domains/daily_report.py`:
  - `AGGREGATED_ENDPOINT = "data/get_aggregated_fitness_data_by_time"`. It moves here, and `passthrough` imports it from here, so the old import path keeps working.
  - `read_daily_report(t: Transport, key: str, start_epoch_s: int, end_epoch_s: int, *, tz: tzinfo | None = None, tag: str = "daily_report") -> PagedResult`. It makes exactly one call, `t.get_paged_result(AGGREGATED_ENDPOINT, {"tag": tag, "key": key, "reverse": True, "limit": 50, "start_time": s, "end_time": e}, list_key="data_list")`, where `(s, e) = report_window(*local_day_span(start_epoch_s, end_epoch_s, tz))`. It validates nothing.
  - `fetch_daily_summaries(t: Transport, key: str, start_epoch_s: int, end_epoch_s: int, *, what: str, summarize: Callable[[str, int, dict], Any], tz: tzinfo | None = None) -> DomainResult`. It reads with `read_daily_report(..., tz=tz)` and then handles each item in this order:
    1. `decode_value(entry)`. On a problem, append that problem's note and skip the row; nothing goes into `raw`.
    2. Append the decoded value to `raw`.
    3. Find the day: `as_int(entry.get("time"))`, then `report_day(...)` and `local_midnight(day, tz)`. A `None` key, or any of `(ValueError, OverflowError, OSError)`, appends the day note and gives no record.
    4. Otherwise append `summarize(day.isoformat(), epoch_s, value)` to `records`.

    After the loop, it appends `stalled_note(f"{what} rows")` when `paged.stalled`.
  - Notes, exact text: `f"Skipped a {what} row whose value is missing or not JSON."` (`UNREADABLE`), `f"Skipped a {what} row whose value is not an object."` (`NOT_AN_OBJECT`), `f"Skipped a {what} row without a usable day timestamp."`.
- Call sites:

  | module | `key` | `what` | `_summary` builds |
  |---|---|---|---|
  | `heart_rate_daily` | `"heart_rate"` | `"daily heart-rate"` | `HeartRateDailySummary`: `avg_hr`, `max_hr`, `min_hr`, `avg_rhr` |
  | `spo2_daily` | `"spo2"` | `"daily SpO2"` | `Spo2DailySummary`: `avg_spo2`, `max_spo2`, `min_spo2` |
  | `stress_daily` | `"stress"` | `"daily stress"` | `StressDailySummary`: `avg_stress`, `max_stress`, `min_stress` |

  - Each `_summary(day: str, epoch_s: int, value: dict)` keeps today's field map, `as_positive_int(value.get(<wire key>))`.
  - `steps.fetch_steps`: `paged = read_daily_report(t, "steps", start_epoch_s, end_epoch_s, tz=tz)`. Its loop, its notes and its `_to_record` stay as they are.
  - `passthrough.fetch_daily_rows`: after its two `_require_text` checks, `paged = read_daily_report(t, key, start_epoch_s, end_epoch_s, tz=tz, tag=tag)`.

- [ ] **Step 1: Pin the note texts that are not pinned yet.** Add to `tests/test_domain_heart_rate_daily.py`:

```python
def test_skip_notes_keep_their_wording():
    payload = payload_with_rows((DAY_1, "not json"), (DAY_2, "[1]"), ("x", figures()))

    result = fetch_heart_rate_daily(PagedFake(payload), START, END, tz=UTC)

    assert result.notes == [
        "Skipped a daily heart-rate row whose value is missing or not JSON.",
        "Skipped a daily heart-rate row whose value is not an object.",
        "Skipped a daily heart-rate row without a usable day timestamp.",
    ]
```

  Add the same test to `tests/test_domain_spo2_daily.py`, with `fetch_spo2_daily`, that file's `figures()` and `"daily SpO2"`. `stress_daily` already pins all three of its notes.
- [ ] **Step 2: Run** `python -m pytest tests/test_domain_heart_rate_daily.py tests/test_domain_spo2_daily.py -q`. Expected: PASS on the current code (these are characterization tests).
- [ ] **Step 3: Write the failing tests** in `tests/test_domain_daily_report.py`.
  - Setup: build rows inline. `row(time, value)` gives `{"time": time, "value": value if isinstance(value, str) else json.dumps(value)}`, and `served(*rows, stalled=False)` gives `PagedFake({"code": 0, "result": {"data_list": list(rows)}}, stalled=stalled)`. Constants: `DAY_1 = 1760400000` (2025-10-14T00:00Z), `DAY_2 = DAY_1 + 86400`, `CST = timezone(timedelta(hours=8))`.
  - `test_read_daily_report_asks_for_the_local_days`: `read_daily_report(t, "steps", DAY_1, DAY_2 + 86399, tz=timezone.utc)` gives `t.calls == [{"endpoint_path": "data/get_aggregated_fitness_data_by_time", "data_obj": {"tag": "daily_report", "key": "steps", "reverse": True, "limit": 50, "start_time": DAY_1 - 86400, "end_time": DAY_2}, "list_key": "data_list", "max_pages": None}]`. The result is a `PagedResult` with `items == []`.
  - `test_read_daily_report_sends_the_tag`: with `tag="daily_fitness"`, `t.calls[0]["data_obj"]["tag"] == "daily_fitness"`; also `passthrough.AGGREGATED_ENDPOINT == daily_report.AGGREGATED_ENDPOINT`.
  - `test_summaries_give_one_record_per_row`:
    - Inputs: `summarize=lambda day, epoch_s, value: (day, epoch_s, value)`, rows `(DAY_2, {"a": 2})` and `(DAY_1, {"a": 1})`, `tz=CST`, window `DAY_1 - 28800` to `DAY_2 - 28800 + 86399`, `key="k"`, `what="daily thing"`.
    - Expected: `records == [("2025-10-15", DAY_2 - 28800, {"a": 2}), ("2025-10-14", DAY_1 - 28800, {"a": 1})]`, `raw == [{"a": 2}, {"a": 1}]`, `notes == []` and `t.calls[0]["data_obj"]["key"] == "k"`.
  - `test_summaries_word_their_notes_with_what`:
    - Inputs: rows `(DAY_1, "not json")`, `(DAY_1, "[1]")` and `("x", {"a": 1})`, with `stalled=True`.
    - Expected: `notes` is the three exact texts above, with `what="daily thing"`, followed by `stalled_note("daily thing rows")`; `records == []` and `raw == [{"a": 1}]`.
  - `test_summaries_skip_a_day_key_out_of_range`, parametrized over `[10**20, -(10**20)]`: the one row `(time, {"a": 1})` gives `records == []`, `raw == [{"a": 1}]` and `notes == ["Skipped a daily thing row without a usable day timestamp."]`.
- [ ] **Step 4: Run** `python -m pytest tests/test_domain_daily_report.py -q`. Expected: FAIL (there is no module `mifit.domains.daily_report`).
- [ ] **Step 5: Implement** `domains/daily_report.py`. Its module docstring says:
  - what a daily report row is: its key is the UTC midnight of the local date, and a window selects `start_time < time <= end_time` (notes/06);
  - what each of the two functions is for;
  - that `steps` and `sleep` keep their own loops, because their notes and day parsing differ.
- [ ] **Step 6: Run** the new file. Expected: PASS.
- [ ] **Step 7: Switch the call sites** (see the table).
  - Each domain module keeps its module docstring and its `fetch_*` docstring.
  - `_ENDPOINT`, `_VALUE_NOTES` and `_to_record` are deleted from the three summary modules, and `_ENDPOINT` from `steps`.
  - Remove any import that is no longer used.
- [ ] **Step 8: Run** `python -m pytest -q`. Expected: everything passes, 2 skipped. Then `git diff -U0 -- tests | grep "^-[^-]"` must print nothing, which shows that no existing test line changed.
- [ ] **Step 9: Commit**: `git add src/mifit/domains tests/test_domain_daily_report.py tests/test_domain_heart_rate_daily.py tests/test_domain_spo2_daily.py && git commit -m "refactor(domains): one daily-report reader for the daily summaries, steps and daily_report"`

---

### Task 2: Data nested too deeply is a clean error (item 2)

**Files:**
- Modify: `src/mifit/cli/__init__.py` (`_write_result`, `_save_part`), `src/mifit/cli/partial.py` (the `_dumps` docstring only), `src/mifit/transport.py` (the `get_json` except clause and docstring), `tests/test_cli.py`, `tests/test_transport.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `_write_result`: the `json.dumps(...)` call goes inside a `try`.
    - On `RecursionError` it calls `_eprint("error: the result is nested too deeply to write as JSON.")` and returns `_EXIT_ERROR` (1). Nothing goes to stdout and no file is written; the notes are still printed first.
    - `_cmd_export` needs no change: its existing "output not written" branch already prints the resume hint when a partial file exists.
  - `_save_part`: `except ValueError as exc` becomes `except (ValueError, RecursionError) as exc` (reason: `type(exc).__name__`). A chunk too deep to store then gets the existing `error: could not save chunk <i>/<n> to <partial> (RecursionError).`
  - `Transport.get_json`: `except ValueError:` becomes `except (ValueError, RecursionError):`. That leads to the existing `ApiError("Response body could not be decrypted or parsed as a JSON object.")`, still raised after the block so it has no chain. The docstring's "an undecryptable body" becomes "an undecryptable body (one nested too deeply to parse included)".

- [ ] **Step 1: Write the failing tests.**
  - In `tests/test_cli.py`, add the helper `indented_dumps_recurse(monkeypatch)`. It patches `json.dumps` so that a call with a non-`None` `indent` raises `RecursionError("maximum recursion depth exceeded")`; any other call goes to the real function. This reproduces what Python 3.12/3.13 do, where the indented encoder is pure Python while the compact one (used by the partial file) is in C. Also add `TOO_DEEP_ERROR = "error: the result is nested too deeply to write as JSON."`. The tests:
    - `test_output_too_deep_to_encode_is_a_clean_error(fake_mifit, monkeypatch, tmp_path, capsys)`: `cli.main(["export", "steps", "--from", "2026-10-01", "--to", "2026-10-02", "--json", str(out)]) == 1`; with `captured = capsys.readouterr()`, `TOO_DEEP_ERROR in captured.err`, `"Traceback" not in captured.err`, `captured.out == ""` and `not out.exists()`.
    - `test_stdout_too_deep_to_encode_prints_no_json`: the same call without `--json` returns 1, prints nothing to stdout, and has `TOO_DEEP_ERROR` in `err`.
    - `test_output_too_deep_keeps_the_partial_for_resume`: with `monkeypatch.setattr(FakeMiFit, "steps", echo())`, `export_steps(out) == 1`. Then `err` holds both `TOO_DEEP_ERROR` and `RESUME_HINT.format(partial_of(out))`, `stored_labels(out) == LABELS`, and `not out.exists()`.
    - `test_chunk_too_deep_to_save_is_a_save_error`: with `echo()` as above and `cli.append_part` patched to raise `RecursionError`, `export_steps(out) == 1`. `err` holds `"error: could not save chunk 1/3"` and `"(RecursionError)"`, and `not partial_of(out).exists()`.
  - In `tests/test_transport.py`:
    - `InvalidJsonHttp.get` now reads its plaintext from a class attribute `plaintext`, which holds today's literal.
    - New: `class TooDeepJsonHttp(InvalidJsonHttp)` with `plaintext = b'{"code": 0, "x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"`.
    - `test_too_deeply_nested_body_is_an_api_error`: `make_transport(TooDeepJsonHttp()).get_json(ENDPOINT, DATA_OBJ)` raises `ApiError`, with `str(exc) == "Response body could not be decrypted or parsed as a JSON object."` and `exception_chain(exc) == [exc]`.
- [ ] **Step 2: Run** `python -m pytest tests/test_cli.py tests/test_transport.py -q`. Expected: the five new tests FAIL, because a `RecursionError` escapes.
- [ ] **Step 3: Implement** the three except clauses. Add one sentence to the `_write_result` docstring: "A result nested too deeply to encode is an error: nothing is written and the exit code is 1." The partial `_dumps` docstring becomes: "Raises ``ValueError`` on a NaN or an infinity, and ``RecursionError`` on nesting too deep to encode, before anything is written."
- [ ] **Step 4: Run** `python -m pytest -q`. Expected: everything passes, 2 skipped.
- [ ] **Step 5: Commit**: `git add src/mifit/cli src/mifit/transport.py tests/test_cli.py tests/test_transport.py && git commit -m "fix(cli,transport): data nested too deeply is a clean error, not a traceback"`

---

### Task 3: Test helpers, guards and `raw` typing (items 3–6)

**Files:**
- Modify:
  - `tests/support.py`;
  - the seven test files `tests/test_domain_heart_rate.py`, `test_domain_heart_rate_daily.py`, `test_domain_spo2.py`, `test_domain_spo2_daily.py`, `test_domain_steps.py`, `test_domain_stress_daily.py` and `test_domain_weight.py`;
  - `src/mifit/domains/base.py` (`DomainResult`), `tests/test_domain_base.py`, `tests/test_domain_catalog.py`, `notes/domain-catalog.md` (§6 guard-test list only), `tests/test_chunks.py`.

**Interfaces:**
- Consumes: Task 1's two added tests call `payload_with_rows` with a `str` value and a `"x"` time. The shared helper supports both.
- Produces in `tests/support.py`:
  - `ABSENT = object()`, which marks a key left out of a row or a value, as opposed to set to `None`.
  - `payload_with_rows_from(template: dict) -> Callable[..., dict]`. It returns `payload_with_rows(*rows)`, which turns `(outer_time, value)` pairs into a fresh `{"code": 0, "result": {"data_list": [...], "has_more": False, "next_key": None}}`.
    - Each row is a deep copy of `template` without its `time`, plus `value` and `time`.
    - `value`: a `str` is used as the wire string itself; anything else is `json.dumps(value)`.
    - `time`: set to `outer_time`, except that `outer_time is ABSENT` leaves the key out.
- `DomainResult.raw` is annotated `list[Any]`.

- [ ] **Step 1: Count.** Run `python -m pytest tests/test_domain_heart_rate.py tests/test_domain_heart_rate_daily.py tests/test_domain_spo2.py tests/test_domain_spo2_daily.py tests/test_domain_steps.py tests/test_domain_stress_daily.py tests/test_domain_weight.py -q` and note the `N passed`.
- [ ] **Step 2: Move the helper.** Add `ABSENT` and `payload_with_rows_from` to `support.py`. In each of the seven files:
  - delete the local `payload_with_rows` and the local `ABSENT = object()`;
  - import `ABSENT, payload_with_rows_from` from `support`;
  - bind the helper once, at module level:
    - the five fixture-based files (heart-rate raw, `heart_rate_daily`, `spo2_daily`, `steps`, `stress_daily`) use `payload_with_rows = payload_with_rows_from(load_fixture()["result"]["data_list"][0])`, placed after `load_fixture`;
    - `test_domain_spo2.py` uses `payload_with_rows_from({"key": "spo2", "sid": "synthetic-sid", "update_time": 1760500000, "zone_name": "Asia/Shanghai", "zone_offset": 28800})`;
    - `test_domain_weight.py` uses the same template with `"key": "weight"` and `"update_time": T + 60`.

  No test body changes. The `payload_with_rows` in `test_domain_workouts.py`, which replaces `sport_records`, is a different helper and stays.
- [ ] **Step 3: Verify the move.**
  - Run Step 1's command again. Expected: the same `N passed`.
  - `grep -rn "def payload_with_rows\b" tests` should list only `tests/test_domain_workouts.py`.
  - `grep -rn "^ABSENT = object()" tests` should list only `tests/support.py`.
- [ ] **Step 4: Type `raw` as `list[Any]`.** Write `test_domain_result_raw_holds_any_rows` in `tests/test_domain_base.py`: `typing.get_type_hints(DomainResult)["raw"] == list[Any]`. Run it; expected: FAIL.

  Then change the annotation (`from typing import Any, NamedTuple`) and add this sentence to the `DomainResult` docstring: "Domains put one decoded ``dict`` per row in ``raw``; the generic reads in ``passthrough`` keep a row that is not an object as it came, so its items are typed ``Any``." Run it; expected: PASS.
- [ ] **Step 5: Add the catalog reverse guard** to `tests/test_domain_catalog.py`:

```python
# MiFit.profile has B records but takes no time window, so it is not in REGISTRY.
UNREGISTERED_B = {"profile"}


def test_every_b_layer_name_in_the_catalog_is_a_domain():
    b_tables = [table for table in _tables() if table[0][-1] == "B 层"]
    named = {
        name
        for table in b_tables
        for row in table[1:]
        for name in re.findall(r"`([^`]+)`", row[-1])
    }

    assert "steps" in named  # the B-layer column was found
    assert sorted(named - set(REGISTRY) - UNREGISTERED_B) == []
```

  - Run it. Expected: PASS.
  - Check that it bites: temporarily change the B cell of the §2 `calories` row to `` 候选 `stress_weekly` ``, rerun, and expect a FAIL that names `stress_weekly`. Then run `git checkout -- notes/domain-catalog.md`.
  - Rewrite the module docstring's promises: every registered domain has a row; every §2/§3 B-layer name is a registered domain or `profile`; the first two cells of each row are unique within the table; no Unix timestamp.
  - Add this line to the guard-test list in catalog §6, after the `REGISTRY` line:

```markdown
  - §2、§3 表格 `B 层` 一格里的每个反引号名都是已注册的数据域（`profile` 除外：它不按时间窗读取，没有注册）；
```
- [ ] **Step 6: Pin sleep's per-chunk raw layout** with a new test in `tests/test_chunks.py`, `test_sleep_merged_raw_is_one_block_per_chunk_in_plan_order(windowed)`:
  - Setup: `client = windowed(sleep_fake())`, `parts = list(client.read_chunks("sleep", START, END))` and `merged = merge_chunks(parts)`.
  - The chunks are the plan: `[chunk for chunk, _ in parts] == plan_chunks("sleep", date.fromisoformat(START), date.fromisoformat(END))`, and there is more than one.
  - Walk an offset through `merged.raw`. For each part:
    - `merged.raw[offset : offset + len(part.raw)] == part.raw`;
    - the block is the chunk's daily values and then its sessions: with `daily = sum("total_duration" in value for value in part.raw)`, `0 < daily < len(part.raw)`, every value in `part.raw[:daily]` has `"total_duration"`, and none in `part.raw[daily:]` does;
    - `part.raw == client.sleep(chunk.first.isoformat(), chunk.last.isoformat()).raw`.
  - At the end, `offset == len(merged.raw)`.

  Run it. Expected: PASS. It pins today's layout; a `merge_chunks` that regrouped sleep's raw would fail it.
- [ ] **Step 7: Run** `python -m pytest -q`. Expected: everything passes, 2 skipped.
- [ ] **Step 8: Commit**: `git add tests src/mifit/domains/base.py notes/domain-catalog.md && git commit -m "test: shared payload_with_rows, catalog reverse guard, sleep chunk raw layout; DomainResult.raw is list[Any]"`

---

### Task 4: Docs wording (items 7–11)

**Files:**
- Modify:
  - `src/mifit/auth/lifetime.py` (the module docstring);
  - `notes/09-session-lifetime-renewal.md` (the two 读法 bullets on `renew_failed` and `renew_network_error`);
  - `tests/test_transport.py` (the comment in `test_network_failure_in_the_hook_propagates_and_keeps_the_renewal`);
  - `README.md`, and `src/mifit/client.py` (the `MiFit.raw` docstring);
  - `docs/superpowers/roadmap.md` (the §1 `workouts` bullet, the §3 M4 结果 line, the §6 M7 row);
  - `notes/domain-catalog.md` (the two §3 weight phrases);
  - `docs/superpowers/plans/2026-10-14-mifit-domain-catalog.md` (the last bullet of Decision 8).

- [ ] **Step 1: Item 7.**
  - `lifetime.py`: replace "(the renewal could not reach the network, so the passToken was never judged). Only ``renew_failed`` can bear on the passToken's lifetime." with "(the renewal did not complete because of a network failure, which can come after the passToken was accepted, so it says nothing about the passToken). Only ``renew_failed`` can bear on the passToken's lifetime, and only as an upper bound on refusals: a failed exchange after an accepted passToken is logged as ``renew_failed`` too."
  - notes/09, the `renew_failed` bullet: append "`renew_failed` 的条数因此只是 passToken 被拒次数的上界。"
  - notes/09, the `renew_network_error` clause: replace "续期没能连上网（OSError），账号服务没有表态，下一次 401 会再试。" with "续期因网络故障（OSError）没有完成，下一次 401 会再试；故障可能出在 serviceLogin 已接受 passToken 之后的 STS 一步，所以它不说明 passToken 是否有效。"
  - `test_transport.py`: the comment becomes "# The renewal did not complete because of a network failure, so there is no verdict to count: with a budget of one, the next 401 may still renew."
- [ ] **Step 2: Item 8 (README).**
  - (a) Three replacements:
    - In the `raw` table row, "the rows of any fitness-data key" → "the rows of any fitness-data or daily-report key".
    - Below the table, "returns the rows of any other key of the fitness-data endpoint," → "returns the rows of any other key of the fitness-data or (with `--daily`) daily-report endpoint,".
    - Under "Any key: `mifit raw`", "Reads any key of the fitness-data endpoint as" → "Reads any key of the fitness-data or (with `--daily`) daily-report endpoint as".
    - The `client.raw` lines stay; they are about `raw` alone and already accurate.
  - (b) Append to the "Two layers" `raw` bullet: "The generic reads are the exception: for `raw_window`, `daily_report` and `mifit raw`, `raw` holds each whole row the service sent for your window, its outer fields included and its `value` decoded."
  - (c) In Known limitations, replace the bold title and the two sentences before `--full` in the "Raw heart-rate windows" bullet. The title becomes "**Raw windows are corrected by the SDK.**", followed by: "The service drops the samples at the start of a raw-data window (the first bucket); every dense raw key checked does this: `heart_rate`, `spo2`, `stress` and `calories` (notes/08, notes/11, `notes/domain-catalog.md`). The `heart_rate`, `spo2` and `weight` reads, `raw_window` and `mifit raw` ask the service for a day before the start and keep only the requested window. `raw(key, ...)` returns the service's answer as it came, for any key, without this correction: use `raw_window` for a complete window." The `--full` sentences stay.
  - `MiFit.raw` docstring: "for bucketed keys such as ``heart_rate`` and ``spo2`` (notes/08, notes/11)" → "for bucketed keys (every dense raw key checked: ``heart_rate``, ``spo2``, ``stress``, ``calories``; notes/08, notes/11, the domain catalog)".
  - (d) After "such as `{"track": False}` for `workout_detail`." add: "`options={"full": True}` is refused with `ValueError`: a full read pages back from each chunk's end, so every chunk would read the same samples again; call `heart_rate(..., full=True)` once instead."
- [ ] **Step 3: Item 9 (roadmap).**
  - The §1 `workouts` bullet: "步数；0 或缺失记为 `None`）" → "步数；0 或缺失记为 `None`，时区偏移除外：0 即 UTC，照实保留）".
  - The §3 M4 结果 line: "步数；0 或缺失为 `None`）" → "步数；0 或缺失为 `None`，时区偏移除外：0 即 UTC，照实保留）".
  - The §6 M7 row: "中高强度分钟数 |" → "中高强度分钟数、活动卡路里 |".
  - Nothing else in the roadmap changes.
- [ ] **Step 4: Item 10 (catalog §3).**
  - The old count-and-age wording of the `weight` record → "`weight` 的记录为 1–10 条、最近 7 天没有".
  - "本账号身体成分全为 0（notes/11 §2）" → "本账号身体成分字段全为 0：是（notes/11 §2）".
- [ ] **Step 5: Item 11.** In Decision 8 of the M7 plan, replace "   - All of these become `候选` rows in the catalog." with "   - In the catalog, daily `valid_stand`, `intensity` and `calories` (§2) and raw `stress` (§3) are `候选` rows. The five keys with little or no recent data are `—` rows, listed in §5 under 只有较早数据的 key. (Corrected in the final polish, 2026-10-15: this line first said every item above became a `候选` row.)"
- [ ] **Step 6: Verify.**
  - `python -m pytest -q`: everything passes, 2 skipped. The catalog guards check the new catalog text too.
  - `grep -rnI "never judged\|没能连上网\|All of these become" src tests notes README.md docs/superpowers/roadmap.md docs/superpowers/plans/2026-10-14-mifit-domain-catalog.md` prints nothing, and a grep of `notes/` for the old count-and-age wording of the weight record prints nothing either.
  - `git ls-files --eol` shows `i/lf w/lf` for every touched file.
- [ ] **Step 7: Commit**: `git add src/mifit/auth/lifetime.py src/mifit/client.py notes/09-session-lifetime-renewal.md notes/domain-catalog.md tests/test_transport.py README.md docs/superpowers/roadmap.md docs/superpowers/plans/2026-10-14-mifit-domain-catalog.md && git commit -m "docs: final polish wording (renewal events, README raw reads, roadmap, catalog, M7 plan)"`

---

## Decisions (made without a human in the loop; confirm when reviewing)

1. **Four tasks, not three.** The `RecursionError` fix changes behavior, so it is its own task (Task 2). A reviewer can then reject it, or the test-hygiene task (Task 3), independently. The tasks share no interfaces. Task 3 runs after Task 1 because both touch two of the same test files.
2. **The reader lives in a new `domains/daily_report.py`, not in `days.py`.** `days.py` is pure date arithmetic with no transport; the reader needs `Transport` and `DomainResult`. The module is named after the tag.
3. **There are two levels: `read_daily_report` (the request) and `fetch_daily_summaries` (the loop).**
   - `steps` joins the request only. Its loop is not byte-identical: one note says "not valid JSON", another "or step count", and its `int()` day parse accepts numeric strings and bools.
   - `sleep` is left alone. It is not on the backlog, and its daily read shares `first`/`last` with its raw read. Joining it later is a one-line change.
4. **Notes are built from `what`.** The three templates reproduce each summary domain's current text exactly. Step 1 pins the `heart_rate_daily` and `spo2_daily` texts before the move (`stress_daily` pinned them already).
5. **`summarize(day, epoch_s, value)` receives the ISO date string,** because every summary model stores `date` as ISO text.
6. **`AGGREGATED_ENDPOINT` moves to `daily_report.py`,** and `passthrough` imports it from there, so the name is still importable from `passthrough` (the tests use it).
7. **`_save_part` also maps `RecursionError`.** The backlog names `_write_result` and the transport. The chunk saver encodes the same data (in C on 3.12/3.13, a few levels deeper than the decoder saw), and it already promises "nothing was written". One clause gives it the existing save error instead of a traceback.
8. **The CLI tests simulate 3.12/3.13 by patching `json.dumps` for `indent` calls only.** The depth at which the real encoder fails depends on the interpreter and the stack size, so a real deep value would make the tests version-dependent. The transport test does use a real 100 000-deep body: `json.loads` reliably raises `RecursionError` at that depth on 3.12–3.14, and `test_domain_passthrough.py` already relies on that.
9. **The shared helper is a factory, `payload_with_rows_from(template)`, bound once per file.** Every call site stays as it is.
   - Its value and time rules are the union of the seven copies. No current call site passed a `str` value to a copy that always encoded (heart-rate raw, steps), and no copy that always set `time` (`heart_rate_daily`, `spo2_daily`, `steps`, `stress_daily`) received `ABSENT` as an outer time, so every test's input is unchanged.
   - Rows are deep copies of the template, because the heart-rate raw fixture holds a list.
   - `ABSENT` moves to `support.py`, so there is one sentinel.
   - The `payload_with_rows` in `test_domain_workouts.py` builds `sport_records` and is not one of the seven.
10. **The reverse guard finds its tables by the header's last cell, `B 层`.** It skips each table's header row and allows `profile` (B records, but not a time-window domain, so not registered). Its bite check is a temporary edit that is reverted before any other catalog change.
11. **`DomainResult.raw` becomes `list[Any]`,** pinned with `typing.get_type_hints`.
12. **Roadmap:** the "0 或缺失" claim about `Workout` appears twice, in the §1 `workouts` bullet and in §3 M4 结果. Both get the same fix; no other roadmap line is touched.
13. **README (c)** names the four keys that were checked and every SDK read that corrects for them. `MiFit.raw`'s docstring gets the same key list, so the code and the README agree.
14. **The M7 plan's Decision 8 is corrected in place,** with a dated marker rather than a separate erratum, so the decision list stays the single record.
15. **Out of scope:** the other `json.loads` call sites (`auth/qr.py`, `auth/session.py`, `fds/cipher.py`) read small, local or fixed-format inputs and are not on the backlog. Neither is the parked `_save_part` Ctrl+C window. `notes/11` §2 described the weight record with the same kind of count-and-age wording item 10 fixes in the catalog. The backlog names only the catalog, but the controller ruled in the final review (2026-10-15) that `notes/11` §2 be reworded to buckets as well, so it was.
16. **Execution:** Native, the user's standing preference. The four tasks are small and independent, and a shipped mistake costs one revertable commit.

## Self-Review

1. **Spec coverage:**
   - Item 1 → Task 1 (with steps on the request only, by Decision 3).
   - Item 2 → Task 2.
   - Items 3, 4, 5 and 6 → Task 3, Steps 2, 4, 5 and 6.
   - Items 7, 8, 9, 10 and 11 → Task 4, Steps 1, 2, 3, 4 and 5.
   - Every backlog sub-point, (a)–(d) of item 8 included, has an exact old → new text. ✓
2. **Step scan:**
   - Every code step names its file, signature and exact strings.
   - The test steps carry their assertions.
   - Each docs step quotes the old and the new text.
   - The refactor's "unchanged" claim is checked mechanically (`git diff -U0 -- tests | grep "^-[^-]"`, and the per-file counts). ✓
3. **Type consistency:** `read_daily_report(t, key, start, end, *, tz, tag) -> PagedResult`, `fetch_daily_summaries(t, key, start, end, *, what, summarize, tz) -> DomainResult`, `summarize(day: str, epoch_s: int, value: dict)`, `AGGREGATED_ENDPOINT`, `payload_with_rows_from(template)`, `ABSENT` and `TOO_DEEP_ERROR` are named the same everywhere they appear. ✓
4. **Review Focus:** five lines, each pinned by a named test in Task 1, 2 or 3. ✓
5. **Proportion:** four tasks for eleven small items. The only code blocks are two short tests and one catalog line, each one where the exact text is the point. ✓
