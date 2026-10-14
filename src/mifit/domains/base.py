"""Shared domain plumbing: the result container and a minimal name registry.

A domain is a function ``fn(transport, start_epoch_s, end_epoch_s) -> DomainResult``
registered under a short name so callers can list and dispatch domains.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class DomainResult:
    """What a domain fetch returns.

    ``records`` are the normalized, provider-neutral rows; ``raw`` is the same
    data as the service sent it (decoded but otherwise untouched); ``notes`` are
    human-readable caveats about this particular result.
    """

    records: list = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


DomainFn = Callable[..., DomainResult]

REGISTRY: dict[str, DomainFn] = {}


def register(name: str) -> Callable[[DomainFn], DomainFn]:
    """Decorator that records a domain function in ``REGISTRY`` under ``name``.

    The function is returned unchanged, so it stays directly callable.
    """

    def decorator(fn: DomainFn) -> DomainFn:
        REGISTRY[name] = fn
        return fn

    return decorator


def as_int(raw: object) -> int | None:
    """A reported whole number (``0`` included), or ``None`` when absent or unusable:
    not a number (a ``bool`` is not one), or a float that is not finite."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    return int(raw)
