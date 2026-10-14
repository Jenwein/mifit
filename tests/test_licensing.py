"""The MIT license, the upstream notice and the attribution headers stay in sync."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "adapted from Mi-Fitness-Sync"


def _leading_comments(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return lines


def _listed() -> list[str]:
    notices = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    return sorted(re.findall(r"^  - (src/mifit/\S+\.py)$", notices, re.M))


def test_license_is_mit_for_jenwein_2026():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Jenwein" in text
    assert "Permission is hereby granted, free of charge" in text


def test_notices_carry_the_upstream_mit_license():
    text = (ROOT / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
    assert "https://github.com/kevinkwee/Mi-Fitness-Sync" in text
    assert re.search(r"^Copyright \(c\) \S", text, re.M)
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_notices_list_the_adapted_files():
    assert _listed() == [
        "src/mifit/auth/qr.py",
        "src/mifit/crypto.py",
        "src/mifit/region.py",
        "src/mifit/transport.py",
    ]


def test_every_listed_file_carries_the_header():
    for path in _listed():
        header = "\n".join(_leading_comments(ROOT / path))
        assert MARKER in header, path
        assert "THIRD_PARTY_NOTICES" in header, path


def test_every_file_with_the_header_is_listed():
    with_header = {
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src/mifit").rglob("*.py")
        if MARKER in "\n".join(_leading_comments(p))
    }
    assert with_header == set(_listed())


def test_headers_keep_module_docstrings():
    for path in _listed():
        text = (ROOT / path).read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(text))
        assert doc, path
        assert "Mi-Fitness-Sync" not in doc, path
