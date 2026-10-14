"""Shared domain plumbing: the result container, a minimal name registry, and
``as_int`` / ``as_positive_int`` / ``as_float``, the domains' common readings of a
reported number.

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


def as_float(raw: object) -> float | None:
    """A reported number (``0`` included) as a ``float``, or ``None`` when absent or
    unusable: not a number (a ``bool`` is not one), or a float that is not finite."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    return float(raw)


def as_positive_int(raw: object) -> int | None:
    """A reported figure as an int; ``None`` unless it is a positive number.

    The service reports ``0`` for a figure it did not measure, so ``0`` means "not
    reported", like an absent, negative or non-number value (``as_int`` rules)."""
    value = as_int(raw)
    return value if value is not None and value > 0 else None
