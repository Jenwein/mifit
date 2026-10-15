"""B-layer records as the MCP server's JSON.

The server gives the records' own field names (the same as the library's and the
CLI's), with one change: times. A day record (one with a ``date``) drops its
``epoch_s``; every other moment becomes a local ISO 8601 time with its UTC offset:
``epoch_s`` becomes ``time``, ``start_epoch_s`` becomes ``start`` and ``end_epoch_s``
becomes ``end``. A record with its own ``utc_offset_s`` (a workout, a sleep session)
shows its times, and those of the records inside it, in that offset; every other
time is shown in the machine's local zone, the zone the local calendar days are
counted in. ``raw`` is never given.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from mifit.domains.days import local_midnight
from mifit.jsonsafe import finite, nonfinite_note, plain

__all__ = [
    "encode",
    "iso_time",
    "local_offset_s",
    "offset_label",
    "present_record",
    "range_header",
    "without_stages",
]

_RENAMED = {"epoch_s": "time", "start_epoch_s": "start", "end_epoch_s": "end"}


def offset_label(offset_s: int) -> str:
    """A UTC offset in seconds as ``"UTC+08:00"`` (``"UTC-03:30"``, ``"UTC+00:00"``)."""
    sign = "-" if offset_s < 0 else "+"
    hours, minutes = divmod(abs(offset_s) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _moment(epoch_s: int, tz: tzinfo | None) -> datetime:
    """``epoch_s`` as an aware datetime in ``tz``, or in the machine's local zone."""
    if tz is None:
        return datetime.fromtimestamp(epoch_s).astimezone()
    return datetime.fromtimestamp(epoch_s, tz)


def local_offset_s(epoch_s: int, tz: tzinfo | None = None) -> int:
    """The UTC offset, in seconds, of ``tz`` (the machine's zone when ``None``) at
    ``epoch_s``."""
    offset = _moment(epoch_s, tz).utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def iso_time(
    epoch_s: int, *, offset_s: int | None = None, tz: tzinfo | None = None
) -> str:
    """``epoch_s`` as ISO 8601 to the second with its offset: in ``offset_s`` when
    given, else in ``tz``, else in the machine's local zone."""
    if offset_s is not None:
        moment = datetime.fromtimestamp(epoch_s, timezone(timedelta(seconds=offset_s)))
    else:
        moment = _moment(epoch_s, tz)
    return moment.isoformat(timespec="seconds")


def present_record(record: Any, *, tz: tzinfo | None = None) -> dict[str, Any]:
    """``record`` (a B-layer dataclass) as a dict with local times (see the module
    docstring); nested records (sleep sessions and their stages) are converted the
    same way and inherit the nearest ``utc_offset_s`` above them."""
    return _convert(plain(record), None, tz)


def _convert(value: Any, offset_s: int | None, tz: tzinfo | None) -> Any:
    if isinstance(value, list):
        return [_convert(item, offset_s, tz) for item in value]
    if not isinstance(value, dict):
        return value
    own = value.get("utc_offset_s")
    if own is not None:
        offset_s = own
    is_day = "date" in value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in _RENAMED:
            if key == "epoch_s" and is_day:
                continue
            out[_RENAMED[key]] = (
                None if item is None else iso_time(item, offset_s=offset_s, tz=tz)
            )
        else:
            out[key] = _convert(item, offset_s, tz)
    return out


def without_stages(sleep_day: dict) -> dict:
    """A copy of a presented sleep day without the sessions' stage timelines."""
    sessions = [
        {key: item for key, item in session.items() if key != "stages"}
        for session in sleep_day.get("sessions", [])
    ]
    return {**sleep_day, "sessions": sessions}


def range_header(
    tool: str, first: date, last: date, *, tz: tzinfo | None = None
) -> dict[str, Any]:
    """The first keys of a ranged result: the tool, the local days it covers and the
    UTC offset of the zone they are counted in (at the first day's local midnight)."""
    midnight = local_midnight(first, tz)
    return {
        "tool": tool,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "timezone": offset_label(local_offset_s(midnight, tz)),
    }


def encode(payload: dict[str, Any]) -> str:
    """``payload`` as compact strict JSON. A NaN or Infinity becomes ``null`` and a
    note in ``payload["notes"]`` says how many, so the change is never silent."""
    cleaned, replaced = finite(payload)
    if replaced:
        cleaned["notes"].append(nonfinite_note(replaced))
    return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
