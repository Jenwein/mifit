"""Packaging: the version, the PyPI metadata and the README PyPI renders."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

import mifit
from mifit import cli
from mcp_fakes import P2_TOOLS
from support import load_release_tool

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_the_installed_distribution_version():
    if mifit.__version__ == "0+unknown":
        pytest.skip("mifit is not installed (pip install -e .)")
    assert mifit.__version__ == _pyproject()["project"]["version"]


def test_installed_version_without_metadata_is_0_unknown(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert mifit._installed_version() == "0+unknown"


def test_cli_version_prints_name_and_version(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == f"mifit {mifit.__version__}\n"


REPO = "https://github.com/Jenwein/mifit"


def test_pyproject_has_the_pypi_metadata():
    project = _pyproject()["project"]
    assert project["readme"] == "README.md"
    assert project["urls"] == {
        "Homepage": REPO,
        "Source": REPO,
        "Issues": f"{REPO}/issues",
        "Changelog": f"{REPO}/blob/main/CHANGELOG.md",
    }
    assert {
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Typing :: Typed",
    } <= set(project["classifiers"])
    # PEP 639: the license is the `license` expression; a License classifier
    # alongside it is an error.
    assert not [c for c in project["classifiers"] if c.startswith("License ::")]
    assert "authors" not in project
    assert project["keywords"]


def test_py_typed_ships_with_the_package():
    assert (ROOT / "src" / "mifit" / "py.typed").is_file()
    assert "py.typed" in _pyproject()["tool"]["setuptools"]["package-data"]["mifit"]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The lines after ``## <heading>`` up to the next ``## `` line."""
    lines = text.splitlines()
    start = lines.index(f"## {heading}") + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines)
    )
    return "\n".join(lines[start:end])


def test_readme_links_are_absolute():
    # PyPI renders the README away from the repository: relative links and
    # in-page anchors break there.
    targets = re.findall(r"\]\(([^)\s]+)\)", _readme())
    assert targets
    assert [t for t in targets if not t.startswith("https://")] == []


def test_readme_install_section_uses_pypi():
    install = _section(_readme(), "Install")
    for command in ("uv tool install mifit", "pipx install mifit", "pip install mifit"):
        assert command in install
    assert "git clone" not in install
    assert "git clone" in _section(_readme(), "Development")


def test_readme_has_the_pypi_badge():
    assert "(https://pypi.org/project/mifit/)" in _readme()


def test_changelog_0_2_0_lists_the_packaging():
    release = load_release_tool()
    section = release.changelog_section(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "0.2.0"
    )
    assert "### Packaging" in section and "uv tool install mifit" in section


# The MCP Registry checks this comment in the README of the PyPI version it lists.
MCP_NAME_MARKER = "<!-- mcp-name: io.github.jenwein/mifit -->"


def test_readme_carries_the_mcp_registry_marker():
    assert _readme().count(MCP_NAME_MARKER) == 1


def test_readme_mcp_section():
    section = _section(_readme(), "Use with an AI assistant (MCP)")
    for text in (
        "uvx mifit mcp",
        "claude mcp add --scope user mifit -- uvx mifit mcp",
        "claude_desktop_config.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
        "--allow-gps",
        "model provider",
        "login_start",
        "uvx mifit login",
    ):
        assert text in section, text
    for name in P2_TOOLS:
        assert f"`{name}`" in section, name
    # The privacy paragraph must not claim more than is true: login writes a file.
    assert "the login tools save the new session" in " ".join(section.split())


def test_version_is_0_3_0_with_its_changelog():
    assert _pyproject()["project"]["version"] == "0.3.0"
    release = load_release_tool()
    notes = release.changelog_section(
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "0.3.0"
    )
    assert "mifit mcp" in notes and "--allow-gps" in notes and "mcp>=2.3,<3" in notes
