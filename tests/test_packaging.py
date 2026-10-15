"""Packaging: the version, the PyPI metadata and the README PyPI renders."""

from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest

import mifit
from mifit import cli

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
