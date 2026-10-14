"""The partial-export store: what a long export has read so far, kept on disk.

A long export reads many chunks, one after the other, and an interrupted run would
lose them all. ``mifit export`` therefore writes each finished chunk to
``<output>.partial`` as soon as it is read, so a later run can pick up from there
instead of asking the service again.

The file is JSON Lines (ASCII, LF), every line strict JSON with compact separators.
Non-ASCII text is written as JSON escapes, as the main output does, so any string the
service sends, a lone surrogate included, can be stored and read back:

* line 1, the header: ``{"mifit_partial": 1, "domain", "from", "to", "chunk"}``, then
  any option that changes what the export reads (``"track"`` for ``workout_detail``):
  the export's own arguments and nothing else, so a resumed run can tell that the
  file belongs to the same export;
* then one line per finished chunk: ``{"chunk": <label>, "records", "raw", "notes"}``.

Each write is flushed and fsynced, so after a crash or power loss the file holds
every chunk finished before it, plus at most one torn line at the end. The file
holds the export's own data (the same data as the output) and those header fields;
no session, token or user id ever goes into it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

from mifit.domains.base import DomainResult

__all__ = [
    "FORMAT",
    "PartialError",
    "append_part",
    "export_header",
    "load_partial",
    "partial_path",
    "start_partial",
]

FORMAT = 1
"""The file format number, written in the header; a change to the format bumps it."""

_PART_KEYS = ("chunk", "records", "raw", "notes")
_CORE_KEYS = ("domain", "from", "to", "chunk")
"""The export arguments every header holds; options such as ``track`` follow them."""
_PLAIN = re.compile(r"[A-Za-z0-9_.:-]{1,40}")


class PartialError(ValueError):
    """The partial file is damaged, or belongs to another export.

    The message names the file and, for another export's header, its stored export
    arguments; it never quotes anything else from the file's content.
    """


def partial_path(json_path: str | Path) -> Path:
    """Where the partial file of the export written to ``json_path`` lives."""
    return Path(f"{json_path}.partial")


def export_header(domain: str, start: str, end: str, chunk: str, **options: Any) -> dict:
    """The header line of an export: its arguments, which a resumed run must repeat.

    ``options`` (such as ``track=False``) follow ``chunk``, in the order given.
    """
    return {
        "mifit_partial": FORMAT,
        "domain": domain,
        "from": start,
        "to": end,
        "chunk": chunk,
        **options,
    }


def _dumps(obj: Any) -> bytes:
    """One JSON Lines line of ``obj``: compact strict JSON, ASCII only (non-ASCII text
    is escaped, so no string can fail to encode), LF-terminated.

    Raises ``ValueError`` on a NaN or an infinity, and ``RecursionError`` on nesting too
    deep to encode, before anything is written.
    """
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return text.encode("ascii") + b"\n"


def _write(path: Path, mode: str, line: bytes) -> None:
    # Binary, so Windows does not turn the LF into CRLF.
    with open(path, mode) as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def start_partial(path: Path, header: dict) -> None:
    """Create ``path`` (or empty it) and write the ``header`` line."""
    _write(path, "wb", _dumps(header))


def append_part(path: Path, label: str, part: DomainResult) -> None:
    """Append the finished chunk ``label`` as one line, flushed and fsynced.

    ``part`` must already be plain data (records as dicts: ``cli._json_ready``).
    Raises ``ValueError`` for a non-finite number, which strict JSON cannot hold;
    the file is left as it was.
    """
    line = _dumps(
        {"chunk": label, "records": part.records, "raw": part.raw, "notes": part.notes}
    )
    _write(path, "ab", line)


def _reject_constant(name: str) -> Any:
    raise ValueError(name)  # NaN, Infinity: not JSON, so not a line this module wrote


def _parse(line: bytes) -> Any | None:
    """The JSON value on ``line``, or ``None`` when it does not parse as strict JSON.

    Returns instead of raising: a ``JSONDecodeError`` keeps the line in ``.doc``, and
    the callers raise their own error outside any ``except`` block.
    """
    try:
        return json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError included
        return None


def _parse_part(line: bytes) -> tuple[str, DomainResult] | None:
    """``(label, part)`` for a well-formed chunk line, else ``None``."""
    doc = _parse(line)
    if not isinstance(doc, dict) or set(doc) != set(_PART_KEYS):
        return None
    label, records, raw, notes = (doc[key] for key in _PART_KEYS)
    if not (isinstance(label, str) and isinstance(records, list) and isinstance(raw, list)):
        return None
    if not (isinstance(notes, list) and all(isinstance(note, str) for note in notes)):
        return None
    return label, DomainResult(records=records, raw=raw, notes=notes)


def _show(value: Any) -> str:
    """A stored header name or value for a message: itself when it is a short plain word,
    date or number (a boolean as JSON spells it), else ``?``, so a hand-edited file
    cannot put control characters on the terminal."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = value if isinstance(value, str) else str(value) if isinstance(value, int) else ""
    return text if _PLAIN.fullmatch(text) else "?"


def _damaged(path: Path) -> PartialError:
    return PartialError(f"{path} is damaged; delete it to start over")


def _foreign_header(path: Path, stored: dict, header: dict) -> PartialError:
    """The error for a stored header that is not ``header``: another version's when
    every export argument of ``header`` matches (an option such as ``track`` that
    the stored header lacks counts as matching: that version did not write it), else
    another export's, naming the stored ``domain``, ``from``, ``to`` and ``chunk``
    (``?`` when missing) and then every other stored field but ``mifit_partial``,
    such as ``track``."""
    same_export = all(
        stored.get(key) == value or (key not in _CORE_KEYS and key not in stored)
        for key, value in header.items()
        if key != "mifit_partial"
    )
    if same_export:
        return PartialError(
            f"{path} was written by another version of mifit; delete it to start over"
        )
    extra = [key for key in stored if key not in _CORE_KEYS and key != "mifit_partial"]
    fields = ", ".join(f"{_show(key)} {_show(stored.get(key))}" for key in [*_CORE_KEYS, *extra])
    return PartialError(
        f"{path} belongs to another export ({fields}); rerun with --resume and those "
        "arguments to continue it, or delete the file to start over"
    )


def load_partial(
    path: Path, header: dict, labels: Collection[str]
) -> dict[str, DomainResult]:
    """The chunks a partial file holds, as ``label -> DomainResult``.

    ``header`` is this export's own (``export_header``) and ``labels`` are the labels
    of its planned chunks. The file must start with exactly that header, else a
    ``PartialError`` says that it is damaged or, for another export's header, which
    export it belongs to. A later line for a label replaces an earlier one.

    A bad last line is taken as torn (a crash in the middle of a write) and dropped,
    and that chunk is simply read again: one that does not parse, lacks a field, has
    a field of the wrong type or has extra keys. Any other bad line, or a label not
    in ``labels``, is a ``PartialError``: the file is damaged, or was made for
    another chunk plan.

    Afterwards the file is safe to append to, so this may write: a dropped tail is cut
    off (truncated), and a last sound line that lost only its newline gets it back
    (written and fsynced). A file that needs neither is not touched.

    Raises ``FileNotFoundError`` for a missing file, and ``OSError`` if the file cannot
    be read or the repair cannot be written.
    """
    path = Path(path)
    data = path.read_bytes()
    lines = data.split(b"\n")
    tail_complete = lines[-1] == b""  # the data ends with an LF (or is empty)
    if tail_complete:
        lines.pop()

    if not lines:
        raise _damaged(path)
    first = _parse(lines[0])
    if first != header:
        if isinstance(first, dict) and "mifit_partial" in first:
            raise _foreign_header(path, first, header)
        raise _damaged(path)

    parts: dict[str, DomainResult] = {}
    keep = len(lines)  # the lines [0, keep) are sound
    for index in range(1, len(lines)):
        parsed = _parse_part(lines[index])
        if parsed is None and index == len(lines) - 1:
            keep = index  # torn: read that chunk again
            break
        if parsed is None or parsed[0] not in labels:
            raise _damaged(path)
        parts[parsed[0]] = parsed[1]

    if keep < len(lines):
        os.truncate(path, sum(len(line) + 1 for line in lines[:keep]))
    elif not tail_complete:
        _write(path, "ab", b"\n")
    return parts
