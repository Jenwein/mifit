"""Long ranges read in chunks: the chunk policy, planning, a serial chunked read
and the merge of its parts.

A chunk is a block of whole local days. Each domain has a size (``DOMAIN_CHUNK``):

* ``heart_rate`` and ``spo2`` (raw points, 144 to 1440 rows a day) use ``week``:
  every raw read asks the service for one day more before its start (the bucket
  margin of ``raw_window``), so a week costs one day of margin where day chunks
  would double every read.
* The daily-report domains (``steps``, ``heart_rate_daily``, ``sleep``,
  ``spo2_daily``, ``stress_daily``, ``activity_daily``) use ``month``: 31 daily
  rows fit one page at ``limit`` 50. ``activity_daily`` reads three keys, so its
  chunk is three such requests.
* ``consumption`` uses ``month``: it sends one request per day either way, so a
  chunk is 31 one-day requests, which is also the step a resumed read restarts from.
* ``workouts`` and ``weight`` use ``month``: their rows are sparse.
* ``workout_detail`` uses ``week``: each workout costs up to three requests and a call
  reads at most 10. A call over a range with more workouts reads the newest 10 and
  notes the rest, so a chunked read of such a range can read more of them than one call.
* A domain missing from the table uses ``week``, the conservative size.

Blocks are fixed lengths counted from the first day (``day`` 1, ``week`` 7,
``month`` 31 days), not calendar weeks or months, so a range that fits in one
block is read in exactly one call, the same call a single read makes.

Chunks are planned newest-first, as the paging domains' ``reverse`` reads return
their rows, except for the domains in ``OLDEST_FIRST`` (``consumption``, whose
single read walks the days oldest-first). Merged in that order
(``merge_chunks``), a chunked read's ``records`` equal one read of the whole
range, order included, and so does its ``raw`` for every domain but ``sleep``
and ``activity_daily``, and for ``workout_detail`` only while each call stays
within its 10-workout cap. A single read of either gives its ``raw`` in
sections: ``sleep`` the daily values and then the matched raw sessions,
``activity_daily`` one section per key. Merged, a chunked read holds those
sections once per chunk, in chunk order: the same values in another order. With
more than one chunk the notes carry their chunk's label, so they differ from a
single read's too.

The raw reads of ``heart_rate``, ``spo2`` and ``weight`` start one day early (the
margin of ``raw_window``), so neighbouring chunks' requests overlap by a day. A row
the service sends there without a usable time cannot be placed in either chunk, so
each read that gets it notes it: a chunked read can note such a row twice where a
single read notes it once. ``records`` and ``raw`` are not affected, because those
rows go into neither.

The chunks are read one at a time through the client's own transport, which
already pauses before every request (``Transport``: ``delay_s`` plus jitter), so
no extra delay is added between chunks.
"""

from __future__ import annotations

from collections.abc import Container, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from mifit.domains.base import REGISTRY, DomainResult
from mifit.domains.days import parse_day

ChunkSize = Literal["day", "week", "month"]

CHUNK_DAYS: dict[ChunkSize, int] = {"day": 1, "week": 7, "month": 31}

DOMAIN_CHUNK: dict[str, ChunkSize] = {
    "steps": "month",
    "heart_rate": "week",
    "heart_rate_daily": "month",
    "consumption": "month",
    "sleep": "month",
    "workouts": "month",
    "workout_detail": "week",
    "spo2_daily": "month",
    "stress_daily": "month",
    "activity_daily": "month",
    "spo2": "week",
    "weight": "month",
}
"""The chunk size of each registered domain (see the module docstring for why)."""

OLDEST_FIRST: frozenset[str] = frozenset({"consumption"})
"""Domains whose single read lists days oldest-first, so their chunks run that way."""


@dataclass(frozen=True)
class Chunk:
    """A block of whole local days, both ends inclusive."""

    first: date
    last: date

    @property
    def label(self) -> str:
        """``"YYYY-MM-DD..YYYY-MM-DD"``: the chunk's first and last day."""
        return f"{self.first.isoformat()}..{self.last.isoformat()}"


def chunk_size_for(domain: str) -> ChunkSize:
    """The chunk size of ``domain``; ``"week"`` for a domain not in the table."""
    return DOMAIN_CHUNK.get(domain, "week")


def split_days(first: date, last: date, size: ChunkSize) -> list[Chunk]:
    """The days ``first`` to ``last`` in blocks of ``size``, in ascending order.

    The blocks count from ``first``: ``[first, first + n - 1]``, then the next
    ``n`` days, and so on, the last one clipped to ``last`` (``n`` is
    ``CHUNK_DAYS[size]``). A range of ``n`` days or fewer is one block.

    Raises ``ValueError`` when ``first`` is after ``last`` or ``size`` is not a
    chunk size.
    """
    if size not in CHUNK_DAYS:
        raise ValueError(
            f"unknown chunk size {size!r}; use one of: {', '.join(CHUNK_DAYS)}"
        )
    if first > last:
        raise ValueError(f"the first day {first} is after the last day {last}")
    span = CHUNK_DAYS[size] - 1
    chunks = []
    block_first = first
    while True:
        # Stepped by what is left, so no date past ``last`` is ever computed.
        block_last = block_first + timedelta(days=min(span, (last - block_first).days))
        chunks.append(Chunk(block_first, block_last))
        if block_last == last:
            return chunks
        block_first = block_last + timedelta(days=1)


def plan_chunks(
    domain: str, first: date, last: date, size: ChunkSize | None = None
) -> list[Chunk]:
    """The chunks a chunked read of ``domain`` takes, in the order it reads them.

    Blocks of ``size`` (``chunk_size_for(domain)`` when ``None``, see
    ``split_days``), newest first unless ``domain`` is in ``OLDEST_FIRST``: the
    order of the domain's own single read.
    """
    chunks = split_days(first, last, size or chunk_size_for(domain))
    return chunks if domain in OLDEST_FIRST else chunks[::-1]


def read_chunks(
    client: Any,
    domain: str,
    start: str,
    end: str,
    *,
    size: ChunkSize | None = None,
    skip: Container[str] = frozenset(),
    options: Mapping[str, Any] | None = None,
) -> Iterator[tuple[Chunk, DomainResult]]:
    """Read ``domain`` from ``start`` to ``end`` chunk by chunk, lazily.

    ``client`` is a ``MiFit`` (anything with the domain methods). ``start`` and
    ``end`` are ``"YYYY-MM-DD"`` local days, both inclusive. The arguments are
    checked here, before anything is read: ``ValueError`` for a domain that is
    not registered, a string that is not a calendar date, ``start`` after
    ``end`` or an unknown ``size``; ``TypeError`` for a date that is not a
    string.

    ``options`` are keyword arguments for the domain method, passed to every
    chunk's call, such as ``{"track": False}`` for ``workout_detail``; they are
    copied here, so a later change to the mapping does not reach the read. A truthy
    ``full`` (``heart_rate``'s backward read) is a ``ValueError``: it pages back from
    each chunk's end without a start, so the chunks would read the same samples
    again, each with many requests.

    Returns a generator over ``(chunk, result)`` in ``plan_chunks`` order. Each
    chunk whose label is in ``skip`` is passed over; every other one is read with
    ``client.<domain>(chunk.first, chunk.last, **options)`` (as ISO dates) only
    when the caller asks for the next part, so nothing is read before the first
    ``next()``. An exception from a chunk's read propagates; the parts already
    yielded are the caller's to keep.
    """
    if domain not in REGISTRY:
        raise ValueError(
            f"unknown domain {domain!r}; known domains: {', '.join(sorted(REGISTRY))}"
        )
    first = _day_arg(start, "start")
    last = _day_arg(end, "end")
    if first > last:
        raise ValueError(f"start {start} is after end {end}")
    options = dict(options or {})
    if options.get("full"):
        raise ValueError(
            "a chunked read cannot take the 'full' option: a full read pages back from "
            "the chunk's end without a start, so every chunk would read the same samples "
            "again; make one call of the domain method instead"
        )
    plan = plan_chunks(domain, first, last, size)
    read = getattr(client, domain)
    return _read_plan(read, plan, skip, options)


def _read_plan(
    read: Any, plan: list[Chunk], skip: Container[str], options: dict[str, Any]
) -> Iterator[tuple[Chunk, DomainResult]]:
    for chunk in plan:
        if chunk.label in skip:
            continue
        yield chunk, read(chunk.first.isoformat(), chunk.last.isoformat(), **options)


def merge_chunks(parts: Sequence[tuple[Chunk, DomainResult]]) -> DomainResult:
    """One result from the parts of a chunked read, in the order of ``parts``.

    ``records`` and ``raw`` are concatenated. For the parts of ``read_chunks``,
    in the order it yields them, ``records`` equal a single read of the whole
    range (for ``workout_detail``, while each call stays within its 10-workout
    cap). ``raw`` does too, except for ``sleep`` and ``activity_daily``: their
    raw comes as one block of sections per chunk, in chunk order (``sleep``: the
    daily values, then the matched sessions; ``activity_daily``: one section per
    key), where a single read gives each section once, for the whole range. With
    more than one part every note is prefixed with its chunk's label
    (``"<label>: <note>"``), so a repeated note says which chunk it came from; a
    single part's notes are kept as they are. (A row without a usable time in the
    day two raw reads share can be noted by both chunks; see the module
    docstring.) No parts give an empty result.
    """
    merged = DomainResult()
    labeled = len(parts) > 1
    for chunk, result in parts:
        merged.records.extend(result.records)
        merged.raw.extend(result.raw)
        merged.notes.extend(
            f"{chunk.label}: {note}" if labeled else note for note in result.notes
        )
    return merged


def _day_arg(value: object, name: str) -> date:
    """``value`` as a local day; chunked reads take ``"YYYY-MM-DD"`` strings only."""
    if not isinstance(value, str):
        raise TypeError(
            f"chunked reads take 'YYYY-MM-DD' dates; {name} is a {type(value).__name__}"
        )
    return parse_day(value, name)
