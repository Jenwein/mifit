"""Domain results as strict JSON data.

``json_ready`` turns a ``DomainResult`` into plain data that strict JSON can hold:
records become dicts and every NaN or Infinity becomes ``null``, with a note saying
how many. The CLI's exports and the MCP server (``mifit.mcp_server``) share these
helpers, so both write the same data the same way.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from mifit.domains.base import DomainResult

__all__ = ["finite", "json_ready", "nonfinite_note", "plain"]


def plain(record: Any) -> Any:
    """A record as JSON-ready data: dataclasses become dicts, the rest is as it is."""
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        return dataclasses.asdict(record)
    return record


def finite(value: Any) -> tuple[Any, int]:
    """A copy of ``value`` with every non-finite float (NaN, +Infinity, -Infinity)
    replaced by ``None``, at any depth of dicts, lists and tuples (the latter
    become lists), and how many were replaced. ``bool`` and ``int`` are untouched.

    ``value`` is JSON-shaped data, a tree (no container holds itself). It is walked
    with an explicit stack, not by recursion, so any depth ``json.loads`` accepts
    works; dict keys and list items keep their order.
    """
    replaced = 0
    root: list[Any] = [None]
    # Each entry: a value to copy, and the container and key (or index) it goes to.
    stack: list[tuple[Any, Any, Any]] = [(value, root, 0)]
    while stack:
        item, target, key = stack.pop()
        if isinstance(item, float):
            if not math.isfinite(item):
                item = None
                replaced += 1
            target[key] = item
        elif isinstance(item, dict):
            # Every key is set now, in order; its value is filled in later.
            copy = dict.fromkeys(item)
            target[key] = copy
            stack.extend((child, copy, child_key) for child_key, child in item.items())
        elif isinstance(item, (list, tuple)):
            copy = [None] * len(item)
            target[key] = copy
            stack.extend((child, copy, index) for index, child in enumerate(item))
        else:
            target[key] = item
    return root[0], replaced


def nonfinite_note(count: int) -> str:
    """The note that says ``count`` non-finite numbers were written as ``null``."""
    return (
        f"Replaced {count} non-finite number(s) (NaN or Infinity) with null: "
        "strict JSON has no such values."
    )


def json_ready(result: DomainResult) -> DomainResult:
    """``result`` as strict-JSON data: records as plain dicts and no NaN/Infinity.

    JSON has no such numbers (``json.dumps`` would write the non-standard ``NaN``
    and ``Infinity`` literals), so each one becomes ``null``; the notes say how
    many, so the change is never silent.
    """
    records, in_records = finite([plain(record) for record in result.records])
    raw, in_raw = finite(result.raw)
    notes = list(result.notes)
    replaced = in_records + in_raw
    if replaced:
        notes.append(nonfinite_note(replaced))
    return DomainResult(records=records, raw=raw, notes=notes)
