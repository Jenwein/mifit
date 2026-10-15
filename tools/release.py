"""Release checks for mifit, run by ``.github/workflows/release.yml``.

Standard library only, so the workflow runs it with a bare Python before
anything is installed. It reads this repository's ``pyproject.toml`` and
``CHANGELOG.md``. Subcommands:

- ``version``: print the project version (``[project].version``).
- ``check-version --tag TAG``: the tag must be ``v<version>`` and the CHANGELOG
  must have a non-empty ``## <version>`` section. Exit 1 otherwise.
- ``check-dist DIR``: ``DIR`` must hold exactly this version's sdist and wheel,
  with no private file in either (see ``dist_problems``), and the wheel must
  carry ``py.typed`` and the ``mifit`` console script. Exit 1 otherwise.
- ``notes VERSION``: print that version's CHANGELOG section (the GitHub
  Release body). Exit 1 if there is none.

Problems go to stderr as ``error: <message>``, one per line.
"""

from __future__ import annotations

import argparse
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
    check_version = commands.add_parser("check-version", help="the tag, pyproject and CHANGELOG agree")
    check_version.add_argument("--tag", required=True)
    check_dist = commands.add_parser("check-dist", help="the built sdist and wheel are clean")
    check_dist.add_argument("dist_dir", type=Path)
    notes = commands.add_parser("notes", help="print a version's CHANGELOG section")
    notes.add_argument("version")
    args = parser.parse_args(argv)

    version = project_version(ROOT / "pyproject.toml")
    if args.command == "version":
        print(version)
        return 0
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if args.command == "check-version":
        return _report(version_problems(args.tag, version, changelog))
    if args.command == "check-dist":
        return _report(dist_problems(args.dist_dir, version))
    section = changelog_section(changelog, args.version)
    if not section:
        return _report([f'CHANGELOG.md has no "## {args.version}" section'])
    print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
