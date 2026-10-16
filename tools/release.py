"""Release checks for mifit, run by ``.github/workflows/release.yml``.

Standard library only, so the workflow runs it with a bare Python before
anything is installed. It reads this repository's ``pyproject.toml``,
``CHANGELOG.md``, ``README.md``, ``server.json`` and the Claude Desktop bundle
sources in ``packaging/mcpb/``. Subcommands:

- ``version``: print the project version (``[project].version``).
- ``check-version --tag TAG [--owner OWNER]``: the tag must be ``v<version>``,
  the CHANGELOG must have a non-empty ``## <version>`` section, ``server.json``
  must carry ``<version>`` twice, the README must declare ``mcp-name: <its
  name>``, and the bundle's manifest and pyproject must carry ``<version>`` and
  pin ``mifit==<version>``. With ``--owner`` (the workflow passes
  ``$GITHUB_REPOSITORY_OWNER``), the ``server.json`` name must also start with
  ``io.github.<OWNER>/``. Exit 1 otherwise.
- ``check-dist DIR``: ``DIR`` must hold exactly this version's sdist and wheel,
  with no private file in either (see ``dist_problems``), and the wheel must
  carry ``py.typed`` and the ``mifit`` console script. Exit 1 otherwise.
- ``check-mcpb PATH``: ``PATH`` must be this version's Claude Desktop bundle,
  ``mifit-<version>.mcpb``, holding exactly ``BUNDLE_MEMBERS``, each with the
  bytes of its source in ``packaging/mcpb/``. Exit 1 otherwise.
- ``notes VERSION``: print that version's CHANGELOG section (the GitHub
  Release body). Exit 1 if there is none.

Problems go to stderr as ``error: <message>``, one per line.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

PRIVATE_DIRS = frozenset(
    {"evidence", "work", "reference", "target", ".mifit", ".superpowers", "secrets", "private"}
)
"""Path components that never belong in a release: research data, local
scratch, sessions, credentials and the orchestration ledger (all git-ignored)."""

CONSOLE_SCRIPT = "mifit = mifit.cli:main"

SERVER_JSON_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
"""The MCP Registry's ``server.json`` schema that ``server.json`` follows."""

BUNDLE_DIR = Path("packaging", "mcpb")
"""The Claude Desktop bundle sources, relative to ``ROOT``."""

BUNDLE_MEMBERS = frozenset({"manifest.json", "pyproject.toml", "src/server.py"})
"""Everything a packed bundle holds: anything else (a local ``.venv``, ``uv.lock``,
a session file) must never ship."""


def project_version(pyproject: Path) -> str:
    """``[project].version`` of ``pyproject``."""
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def changelog_section(text: str, version: str) -> str | None:
    """The body of the ``## <version>`` section of a CHANGELOG, stripped.

    It runs from the line after the heading to the next ``## `` heading or the
    end of ``text``. The heading must be ``## <version>`` followed by whitespace
    or the end of the line. ``None`` when there is no such heading.
    """
    heading = re.compile(rf"^## {re.escape(version)}(?=\s|$)")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if heading.match(line):
            body: list[str] = []
            for following in lines[index + 1 :]:
                if following.startswith("## "):
                    break
                body.append(following)
            return "\n".join(body).strip()
    return None


def version_problems(tag: str, version: str, changelog: str) -> list[str]:
    """What keeps ``tag`` from being released as ``version``; empty when nothing does."""
    problems: list[str] = []
    if tag != f"v{version}":
        problems.append(f"tag {tag} does not match pyproject version {version} (expected v{version})")
    if not changelog_section(changelog, version):
        problems.append(f'CHANGELOG.md has no "## {version}" section (or it is empty)')
    return problems


_SERVER_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
)


def mcp_name_declared(readme: str, name: str) -> bool:
    """Whether ``readme`` declares ``mcp-name: <name>`` the way the MCP Registry
    checks it (``containsMCPNameToken``, registry v1.8.1).

    Some occurrence of the token must be followed by the end of the text, a
    character that cannot continue a server name, or a comment close (``-->`` /
    ``--!>``). The comparison is case-sensitive, as the registry's is.
    """
    token = f"mcp-name: {name}"
    start = readme.find(token)
    while start >= 0:
        rest = readme[start + len(token) :]
        if not rest or rest[0] not in _SERVER_NAME_CHARS or rest.startswith(("-->", "--!>")):
            return True
        start = readme.find(token, start + 1)
    return False


def server_json_problems(server: dict, version: str, readme: str) -> list[str]:
    """What keeps ``server.json`` (parsed as ``server``) from being published with
    ``version``; empty when nothing does. ``readme`` is the README PyPI shows."""
    problems: list[str] = []
    if server.get("version") != version:
        problems.append(
            f"server.json has version {server.get('version')}; expected {version} (pyproject)"
        )
    for package in server.get("packages", []):
        if package.get("version") != version:
            problems.append(
                f"server.json package {package.get('identifier')} has version "
                f"{package.get('version')}; expected {version} (pyproject)"
            )
    name = server.get("name", "")
    if not mcp_name_declared(readme, name):
        problems.append(
            f"README.md does not declare `mcp-name: {name}` (the server.json name), "
            "so the MCP Registry would reject it"
        )
    return problems


def owner_problems(name: str, owner: str) -> list[str]:
    """What keeps the registry from granting ``name`` to a workflow of a repository
    owned by ``owner``; empty when nothing does.

    The registry grants ``io.github.<owner>/*`` with the owner spelled as GitHub
    spells it, and compares case-sensitively (registry issue #689).
    """
    if owner and name.startswith(f"io.github.{owner}/"):
        return []
    return [
        f"server.json name {name} is not under io.github.{owner}/ "
        f"(the repository owner, $GITHUB_REPOSITORY_OWNER = {owner!r}), "
        "so the MCP Registry would refuse it"
    ]


def bundle_source_problems(manifest: dict, bundle_pyproject: dict, version: str) -> list[str]:
    """What keeps the bundle sources (the parsed ``manifest.json`` and
    ``pyproject.toml`` of ``BUNDLE_DIR``) from being packed for ``version``;
    empty when nothing does."""
    manifest_path = (BUNDLE_DIR / "manifest.json").as_posix()
    pyproject_path = (BUNDLE_DIR / "pyproject.toml").as_posix()
    project = bundle_pyproject.get("project", {})
    problems: list[str] = []
    if manifest.get("version") != version:
        problems.append(
            f"{manifest_path} has version {manifest.get('version')}; expected {version} (pyproject)"
        )
    if project.get("version") != version:
        problems.append(
            f"{pyproject_path} has version {project.get('version')}; expected {version} (pyproject)"
        )
    if project.get("dependencies") != [f"mifit=={version}"]:
        problems.append(
            f"{pyproject_path} depends on {project.get('dependencies')}; "
            f'expected ["mifit=={version}"]'
        )
    return problems


def bundle_problems(bundle: Path, version: str, sources: Path) -> list[str]:
    """What is wrong with the packed bundle ``bundle``, packed from ``sources``
    (``ROOT / BUNDLE_DIR``); empty when nothing is."""
    expected_name = f"mifit-{version}.mcpb"
    problems: list[str] = []
    if bundle.name != expected_name:
        problems.append(f"bundle {bundle.name} should be named {expected_name}")
    with zipfile.ZipFile(bundle) as packed:
        members = {name for name in packed.namelist() if not name.endswith("/")}
        manifest = (
            json.loads(packed.read("manifest.json")) if "manifest.json" in members else {}
        )
        differs = [
            name
            for name in sorted(BUNDLE_MEMBERS & members)
            if packed.read(name) != (sources / name).read_bytes()
        ]
    problems += [f"{bundle.name} lacks {name}" for name in sorted(BUNDLE_MEMBERS - members)]
    problems += [
        f"{bundle.name} holds an extra file: {name}" for name in sorted(members - BUNDLE_MEMBERS)
    ]
    problems += [
        f"{bundle.name}: {name} differs from {(BUNDLE_DIR / name).as_posix()}"
        for name in differs
    ]
    if "manifest.json" in members and manifest.get("version") != version:
        problems.append(
            f"{bundle.name}: manifest.json has version {manifest.get('version')}; "
            f"expected {version}"
        )
    return problems


def _private(member: str) -> bool:
    path = PurePosixPath(member)
    return (
        any(part in PRIVATE_DIRS for part in path.parts)
        or member.endswith((".partial", ".session.json"))
        or path.name == "session.json"
    )


def dist_problems(dist_dir: Path, version: str) -> list[str]:
    """What is wrong with the built distributions in ``dist_dir``; empty when nothing is."""
    sdist_name = f"mifit-{version}.tar.gz"
    wheel_name = f"mifit-{version}-py3-none-any.whl"
    problems: list[str] = []

    present = sorted(p.name for p in dist_dir.iterdir())
    for name in present:
        if name not in (sdist_name, wheel_name):
            problems.append(f"unexpected file in {dist_dir}: {name}")
    for name in (sdist_name, wheel_name):
        if name not in present:
            problems.append(f"missing from {dist_dir}: {name}")

    if sdist_name in present:
        with tarfile.open(dist_dir / sdist_name, "r:gz") as tar:
            members = tar.getnames()
        problems += [f"{sdist_name} holds a private file: {m}" for m in members if _private(m)]

    if wheel_name in present:
        with zipfile.ZipFile(dist_dir / wheel_name) as whl:
            members = whl.namelist()
            entry_points_name = f"mifit-{version}.dist-info/entry_points.txt"
            entry_points = (
                whl.read(entry_points_name).decode("utf-8")
                if entry_points_name in members
                else ""
            )
        problems += [f"{wheel_name} holds a private file: {m}" for m in members if _private(m)]
        for required in ("mifit/__init__.py", "mifit/py.typed"):
            if required not in members:
                problems.append(f"{wheel_name} lacks {required}")
        if CONSOLE_SCRIPT not in (line.strip() for line in entry_points.splitlines()):
            problems.append(f"{wheel_name} lacks the console script `{CONSOLE_SCRIPT}`")

    return problems


def _report(problems: list[str]) -> int:
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools/release.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the project version")
    check_version = commands.add_parser(
        "check-version",
        help="the tag and every version place agree; with --owner, the registry name too",
        description=(
            "The tag is v<version> (pyproject.toml); CHANGELOG.md has that version's "
            "section; server.json carries the version twice and the README marker names "
            "it; packaging/mcpb (manifest.json, pyproject.toml) carries the version and "
            "pins mifit==<version>. With --owner, the server.json name is under "
            "io.github.<OWNER>/."
        ),
    )
    check_version.add_argument("--tag", required=True)
    check_version.add_argument(
        "--owner", help="the GitHub repository owner, exactly ($GITHUB_REPOSITORY_OWNER)"
    )
    check_dist = commands.add_parser("check-dist", help="the built sdist and wheel are clean")
    check_dist.add_argument("dist_dir", type=Path)
    check_mcpb = commands.add_parser("check-mcpb", help="the packed Claude Desktop bundle is clean")
    check_mcpb.add_argument("bundle", type=Path)
    notes = commands.add_parser("notes", help="print a version's CHANGELOG section")
    notes.add_argument("version")
    args = parser.parse_args(argv)

    version = project_version(ROOT / "pyproject.toml")
    if args.command == "version":
        print(version)
        return 0
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if args.command == "check-version":
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        bundle = ROOT / BUNDLE_DIR
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        bundle_pyproject = tomllib.loads((bundle / "pyproject.toml").read_text(encoding="utf-8"))
        owner = [] if args.owner is None else owner_problems(server.get("name", ""), args.owner)
        return _report(
            version_problems(args.tag, version, changelog)
            + server_json_problems(server, version, readme)
            + owner
            + bundle_source_problems(manifest, bundle_pyproject, version)
        )
    if args.command == "check-dist":
        return _report(dist_problems(args.dist_dir, version))
    if args.command == "check-mcpb":
        return _report(bundle_problems(args.bundle, version, ROOT / BUNDLE_DIR))
    section = changelog_section(changelog, args.version)
    if not section:
        return _report([f'CHANGELOG.md has no "## {args.version}" section'])
    print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
