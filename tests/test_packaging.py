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
