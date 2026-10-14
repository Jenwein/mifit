"""Generic reads for any key: the A layer of the SDK, without a normalized record.

``fetch_raw_rows`` reads a raw key (``get_fitness_data_by_time``) and
``fetch_daily_rows`` reads a daily-report key (``get_aggregated_fitness_data_by_time``).
Both return the service's own rows in ``DomainResult.raw``, outer fields intact and
the JSON-string ``value`` decoded, and leave ``records`` empty: a key becomes a
record only when a domain models it. They are not registered in ``REGISTRY``; the
facade offers them as ``MiFit.raw_window`` and ``MiFit.daily_report``.

Raw reads ask a day early, through ``fetch_raw_window``, because the service drops a
window's first bucket (seen on ``heart_rate``, ``spo2``, ``stress`` and ``calories``;
notes/08, notes/11, the domain catalog). Applying that to every key only ever adds
rows that are then filtered. The filter is on the row's outer ``time`` as served.
For the daily-type raw keys, whose outer ``time`` is the UTC midnight of the local
date, that means a row west of UTC can fall in the neighboring local day's window.

The key list, with what each key holds, lives in ``notes/domain-catalog.md``.
"""

from __future__ import annotations

import json
from datetime import tzinfo

from mifit.domains.base import DomainResult, stalled_note
# ``AGGREGATED_ENDPOINT`` is re-exported, so importing it from here keeps working.
from mifit.domains.daily_report import AGGREGATED_ENDPOINT, read_daily_report
from mifit.domains.raw_window import fetch_raw_window
from mifit.transport import Transport


def fetch_raw_rows(
    t: Transport, key: str, start_epoch_s: int, end_epoch_s: int
) -> DomainResult:
    """Read raw ``key`` rows between two Unix-second timestamps, both inclusive.

    One windowed read (``fetch_raw_window``). ``raw`` holds the rows whose outer
    ``time`` is in the window, in the service's order, each with its ``value``
    decoded (see ``_decode_rows``); ``records`` is empty. The notes say, in this
    order, how many rows had no usable outer ``time``, how many values could not be
    decoded, and whether the read stalled on an unusable cursor.

    Raises ``ValueError`` before any request when ``key`` is not a non-empty string.
    The key is not checked against a list of known keys.
    """
    _require_text("key", key)
    window = fetch_raw_window(t, key, start_epoch_s, end_epoch_s)
    rows, undecodable = _decode_rows(window.kept)

    result = DomainResult(raw=rows)
    if window.unusable:
        result.notes.append(
            f"Skipped {window.unusable} '{key}' row(s) without a usable time."
        )
    _note_undecodable(result, key, undecodable)
    if window.stalled:
        result.notes.append(stalled_note(f"'{key}' rows"))
    return result


def fetch_daily_rows(
    t: Transport,
    key: str,
    start_epoch_s: int,
    end_epoch_s: int,
    *,
    tag: str = "daily_report",
    tz: tzinfo | None = None,
) -> DomainResult:
    """Read the ``tag`` daily rows of ``key`` for every local day from start to end.

    Days are calendar days in ``tz``, the machine's local zone by default: the first
    is the one holding ``start_epoch_s`` and the last the one holding
    ``end_epoch_s``, both inclusive, selected the way steps selects its days
    (``days.report_window``). ``tag`` is ``"daily_report"`` for the daily summaries
    and ``"daily_fitness"`` for ``goal``.

    ``raw`` holds every row the service sent, in its order, each with its ``value``
    decoded; a row that is not an object is kept as it came. ``records`` is empty.
    The notes say how many values could not be decoded, then whether the read
    stalled on an unusable cursor.

    Raises ``ValueError`` before any request when ``key`` or ``tag`` is not a
    non-empty string. Neither is checked against a list of known names.
    """
    _require_text("key", key)
    _require_text("tag", tag)
    paged = read_daily_report(t, key, start_epoch_s, end_epoch_s, tz=tz, tag=tag)
    rows, undecodable = _decode_rows(paged.items)

    result = DomainResult(raw=rows)
    _note_undecodable(result, key, undecodable)
    if paged.stalled:
        result.notes.append(stalled_note(f"'{key}' daily rows"))
    return result


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _decode_rows(rows: list) -> tuple[list, int]:
    """Copies of ``rows`` with each ``value`` decoded, and how many could not be.

    A row that is not an object is kept as it came. For an object whose ``value`` is
    a string, any JSON type counts as decoded (an array, a string, a number: the A
    layer keeps what the service sent, unlike ``decode_value``, which wants an
    object). A string that is not JSON, or that Python cannot parse (an integer too
    long, nesting too deep), stays a string and is counted. A ``value`` that is
    absent or not a string is left alone. The input rows are never changed.
    """
    decoded = []
    undecodable = 0
    for row in rows:
        if not isinstance(row, dict):
            decoded.append(row)
            continue
        value = row.get("value")
        if not isinstance(value, str):
            decoded.append(dict(row))
            continue
        try:
            decoded.append({**row, "value": json.loads(value)})
        except (ValueError, RecursionError):
            decoded.append(dict(row))
            undecodable += 1
    return decoded, undecodable


def _note_undecodable(result: DomainResult, key: str, undecodable: int) -> None:
    if undecodable:
        result.notes.append(
            f"Kept {undecodable} '{key}' row(s) whose value could not be decoded "
            "as JSON, as the service sent it."
        )
