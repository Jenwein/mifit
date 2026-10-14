"""Guard tests for the domain catalog (``notes/domain-catalog.md``).

The catalog is written by hand and kept up over time. These tests hold it to two
promises: every registered domain has a row, and nothing from the account (a Unix
timestamp) slips into it. Only the Markdown tables are parsed: a table is a run of
lines starting with ``|``, and its rows are those lines without the ``|---|``
separator. The last cell of a row names the SDK entry (§1) or the B domain (§2, §3).
"""

from __future__ import annotations

import re
from pathlib import Path

import mifit.client  # noqa: F401  (importing the client registers the domains)
from mifit.domains.base import REGISTRY

CATALOG = Path(__file__).parents[1] / "notes" / "domain-catalog.md"
SEPARATOR = re.compile(r"\|(\s*:?-+:?\s*\|)+")
# A Unix time in seconds from 2020-09 to 2027-01. Digit lookarounds rather than
# ``\b``: next to a CJK character ``\b`` does not match, and the catalog is Chinese.
UNIX_TIME = re.compile(r"(?<!\d)1[67]\d{8}(?!\d)")


def _text() -> str:
    return CATALOG.read_text(encoding="utf-8")


def _tables() -> list[list[list[str]]]:
    """Each table as a list of rows (header included), each row as its cells."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] | None = None
    for line in _text().splitlines():
        line = line.strip()
        if not line.startswith("|"):
            current = None
            continue
        if current is None:
            current = []
            tables.append(current)
        if SEPARATOR.fullmatch(line):
            continue
        current.append([cell.strip() for cell in line.strip("|").split("|")])
    return tables


def test_every_registered_domain_is_in_the_catalog():
    named = {
        name
        for table in _tables()
        for row in table
        for name in re.findall(r"`([^`]+)`", row[-1])
    }

    assert REGISTRY, "importing mifit.client registers the domains"
    assert sorted(set(REGISTRY) - named) == []


def test_catalog_rows_are_unique():
    tables = _tables()

    assert tables
    for table in tables:
        pairs = [tuple(row[:2]) for row in table]
        assert sorted({pair for pair in pairs if pairs.count(pair) > 1}) == []


def test_catalog_holds_no_numbers_from_the_account():
    # Every line, not only the table cells: the catalog holds no example values.
    leaks = [line for line in _text().splitlines() if UNIX_TIME.search(line)]

    assert leaks == []
