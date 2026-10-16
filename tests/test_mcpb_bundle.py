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
from mifit.mcp_server import errors, server, tools
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


SWITCH = MANIFEST["user_config"]["allow_gps"]["title"]


@pytest.mark.parametrize(
    "name", ["GPS_DISABLED", "PRIVACY_NOTE", "INCLUDE_TRACK_DOC", "get_workout_detail"]
)
def test_every_gps_hint_names_the_flag_and_the_extensions_switch(name):
    # An extension user cannot pass --allow-gps; they turn on the switch instead.
    texts = {
        "GPS_DISABLED": errors.GPS_DISABLED,
        "PRIVACY_NOTE": server.PRIVACY_NOTE,
        "INCLUDE_TRACK_DOC": tools.INCLUDE_TRACK_DOC,
        "get_workout_detail": {t.name: t.description for t in tools.DATA_TOOLS}[
            "get_workout_detail"
        ],
    }
    assert "--allow-gps" in texts[name] and SWITCH in texts[name]


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


SERVER = [sys.executable, "-m", "mifit.cli", "mcp"]


def test_handshake_lists_this_servers_tools():
    info, names = HANDSHAKE.handshake(SERVER)
    assert names == P2_TOOLS
    assert (info["name"], info["version"]) == ("mifit", mifit.__version__)


def test_handshake_fails_on_a_server_that_exits(capsys):
    assert HANDSHAKE.main([sys.executable, "-c", "pass"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_handshake_checks_the_expected_version(capsys):
    assert HANDSHAKE.main(["--expect-version", mifit.__version__, *SERVER]) == 0
    assert f"mifit {mifit.__version__}" in capsys.readouterr().out
    assert HANDSHAKE.main(["--expect-version", "9.9.9", *SERVER]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "9.9.9" in err and mifit.__version__ in err


# Answers like an MCP server, but its initialize result has no serverInfo.
FAKE_SERVER = """\
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["id"] == 1:
        result = {"protocolVersion": "2025-06-18", "capabilities": {}}
    else:
        result = {"tools": [{"name": "get_status"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


def test_handshake_names_a_missing_server_info(capsys):
    assert HANDSHAKE.main(["--expect-version", "0.3.2", sys.executable, "-c", FAKE_SERVER]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "0.3.2" in err


@pytest.mark.parametrize("argv", [[], ["--expect-version"], ["--expect-version", "0.3.2"]])
def test_handshake_usage_errors(argv, capsys):
    assert HANDSHAKE.main(argv) == 1
    assert capsys.readouterr().err.startswith("error: usage")
