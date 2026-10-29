"""``server.json``: what the official MCP Registry lists for mifit.

The release workflow publishes this file to the registry after PyPI. The
registry checks the name against the publisher's GitHub identity and the
README of the PyPI version it lists, so these tests pin the exact values.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from mifit import mcp_server
from support import load_release_tool

release = load_release_tool()

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_server_json_uses_the_current_registry_schema():
    assert SERVER["$schema"] == release.SERVER_JSON_SCHEMA


def test_server_name_uses_the_github_owner_exactly():
    # The registry grants io.github.<owner>/* with GitHub's spelling and compares
    # case-sensitively (registry issue #689): "jenwein" would get 403.
    owner = PYPROJECT["project"]["urls"]["Source"].split("/")[3]
    assert owner == "Jenwein"
    assert SERVER["name"] == f"io.github.{owner}/mifit"


def test_server_json_fields():
    assert set(SERVER) == {
        "$schema",
        "name",
        "title",
        "description",
        "version",
        "repository",
        "packages",
    }
    assert 1 <= len(SERVER["description"]) <= 100 and 1 <= len(SERVER["title"]) <= 100
    assert SERVER["repository"] == {"url": "https://github.com/Jenwein/mifit", "source": "github"}
    stdio, http = SERVER["packages"]
    assert stdio == {
        "registryType": "pypi",
        "identifier": "mifit",
        "version": SERVER["version"],
        "runtimeHint": "uvx",
        "transport": {"type": "stdio"},
        "packageArguments": [{"type": "positional", "value": "mcp"}],
    }
    assert {key: http[key] for key in ("registryType", "identifier", "version", "runtimeHint")} == {
        "registryType": "pypi",
        "identifier": "mifit",
        "version": SERVER["version"],
        "runtimeHint": "uvx",
    }


def test_http_package_runs_the_http_server_with_its_defaults():
    # A client that installs this package starts `mifit mcp --transport http` itself
    # and connects to it on loopback: the URL must be the one the server listens on.
    [http] = [p for p in SERVER["packages"] if p["transport"]["type"] != "stdio"]
    assert http["transport"] == {
        "type": "streamable-http",
        "url": f"http://{mcp_server.HTTP_HOST}:{{--port}}{mcp_server.HTTP_PATH}",
    }
    positional, transport, port = http["packageArguments"]
    assert positional == {"type": "positional", "value": "mcp"}
    assert transport == {"type": "named", "name": "--transport", "value": "http"}
    assert port["type"] == "named" and port["name"] == "--port"
    assert port["default"] == str(mcp_server.HTTP_PORT) and port["format"] == "number"
    assert "value" not in port  # the client may choose another port


def test_server_json_never_turns_on_gps():
    assert "--allow-gps" not in (ROOT / "server.json").read_text(encoding="utf-8")
