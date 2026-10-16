"""The Claude Desktop bundle (``packaging/mcpb/``) and ``tools/mcp_handshake.py``.

The bundle is MCPB manifest 0.4, server type ``uv``: Claude Desktop installs the
pinned ``mifit`` with uv and runs ``src/server.py``, which starts ``mifit mcp``.
The release workflow packs it, checks it and starts it with the handshake tool.
"""

from __future__ import annotations

import json
import runpy
import sys
import tomllib
from pathlib import Path

import pytest

import mifit.cli
from mcp_fakes import P2_TOOLS
from support import load_script

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "packaging" / "mcpb"
REPO = "https://github.com/Jenwein/mifit"
MANIFEST = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
ENTRY = load_script(BUNDLE / "src" / "server.py", "mcpb_entry")
HANDSHAKE = load_script(ROOT / "tools" / "mcp_handshake.py", "mcp_handshake")


def test_manifest_is_a_0_4_uv_bundle_that_runs_the_entry_with_the_switch():
    assert MANIFEST["manifest_version"] == "0.4"
    assert MANIFEST["server"] == {
        "type": "uv",
        "entry_point": "src/server.py",
        "mcp_config": {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                "${__dirname}",
                "src/server.py",
                "${user_config.allow_gps}",
            ],
        },
    }  # no "env": Claude Desktop does not pass it on (mcpb #297)


def test_gps_switch_is_off_by_default():
    option = MANIFEST["user_config"]["allow_gps"]
    assert set(MANIFEST["user_config"]) == {"allow_gps"}
    assert (option["type"], option["title"], option["default"], option["required"]) == (
        "boolean",
        "Allow GPS tracks",
        False,
        False,
    )
    assert "where you live" in option["description"]


def test_manifest_lists_the_servers_tools():
    names = [t["name"] for t in MANIFEST["tools"]]
    assert len(names) == len(set(names)) and set(names) == P2_TOOLS
    assert all(t["description"] for t in MANIFEST["tools"])


def test_manifest_metadata():
    assert MANIFEST["name"] == "mifit" and MANIFEST["license"] == "MIT"
    assert MANIFEST["author"] == {"name": "mifit contributors", "url": REPO}
    assert MANIFEST["repository"] == {"type": "git", "url": REPO}
    assert MANIFEST["compatibility"] == {
        "platforms": ["darwin", "linux", "win32"],
        "runtimes": {"python": ">=3.12"},
    }
    assert "signature" not in MANIFEST  # Claude Desktop rejects signed bundles (mcpb #278)


def test_bundle_pyproject_pins_this_release_and_builds_nothing():
    bundle = tomllib.loads((BUNDLE / "pyproject.toml").read_text(encoding="utf-8"))
    version = PYPROJECT["project"]["version"]
    assert bundle["project"]["dependencies"] == [f"mifit=={version}"]
    assert bundle["project"]["requires-python"] == ">=3.12" and "build-system" not in bundle


@pytest.mark.parametrize(
    "argv", [[], ["false"], ["True"], ["1"], ["${user_config.allow_gps}"], ["true", "x"]]
)
def test_entry_turns_on_gps_only_for_exactly_true(argv):
    assert ENTRY.mifit_args(argv) == ["mcp"]
    assert ENTRY.mifit_args(["true"]) == ["mcp", "--allow-gps"]


def test_entry_runs_mifit_mcp(monkeypatch):
    seen = []
    monkeypatch.setattr(mifit.cli, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(sys, "argv", ["server.py", "false"])
    with pytest.raises(SystemExit) as exit_:
        runpy.run_path(str(BUNDLE / "src" / "server.py"), run_name="__main__")
    assert exit_.value.code == 0 and seen == [["mcp"]]


def test_packaging_folder_is_not_a_python_package():
    # A regular package called "packaging" here could shadow the packaging library.
    assert not list((ROOT / "packaging").rglob("__init__.py"))


def test_handshake_lists_this_servers_tools():
    assert HANDSHAKE.handshake([sys.executable, "-m", "mifit.cli", "mcp"]) == P2_TOOLS


def test_handshake_fails_on_a_server_that_exits(capsys):
    assert HANDSHAKE.main([sys.executable, "-c", "pass"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
