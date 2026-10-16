"""``tools/release.py``: the checks the release workflow runs before publishing."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from support import load_release_tool

release = load_release_tool()

TEXT = (
    "# Changelog\n\nintro\n\n"
    "## 0.3.0 — 2026-11-01\n\n### Added\n\n- b\n\n"
    "## 0.2.0 — 2026-10-15\n\n- a\n"
)

ENTRY_POINTS = "[console_scripts]\nmifit = mifit.cli:main\n"

DEFAULT_SDIST = (
    "mifit-{v}/PKG-INFO",
    "mifit-{v}/pyproject.toml",
    "mifit-{v}/src/mifit/__init__.py",
)
DEFAULT_WHEEL = {
    "mifit/__init__.py": "",
    "mifit/py.typed": "",
    "mifit-{v}.dist-info/entry_points.txt": ENTRY_POINTS,
}


def _dist(
    tmp_path: Path,
    version: str = "0.2.0",
    sdist: tuple[str, ...] = DEFAULT_SDIST,
    wheel: dict[str, str] | None = None,
    *,
    make_sdist: bool = True,
    make_wheel: bool = True,
) -> Path:
    """A dist directory with a real sdist and wheel holding ``sdist`` / ``wheel``
    members ("{v}" in a name becomes ``version``)."""
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    if make_sdist:
        with tarfile.open(dist / f"mifit-{version}.tar.gz", "w:gz") as tar:
            for name in sdist:
                info = tarfile.TarInfo(name.format(v=version))
                tar.addfile(info, io.BytesIO(b""))
    if make_wheel:
        members = DEFAULT_WHEEL if wheel is None else wheel
        with zipfile.ZipFile(dist / f"mifit-{version}-py3-none-any.whl", "w") as whl:
            for name, content in members.items():
                whl.writestr(name.format(v=version), content)
    return dist


def test_changelog_section_returns_the_body_up_to_the_next_heading():
    assert release.changelog_section(TEXT, "0.3.0") == "### Added\n\n- b"
    assert release.changelog_section(TEXT, "0.2.0") == "- a"  # last section: to the end
    assert release.changelog_section(TEXT, "0.4.0") is None


def test_changelog_section_needs_the_exact_version():
    text = "## 0.2.0.1 — x\n\n- no\n\n## 0.2.01\n\n- no\n"
    assert release.changelog_section(text, "0.2.0") is None


def test_version_problems_empty_when_tag_pyproject_and_changelog_agree():
    assert release.version_problems("v0.3.0", "0.3.0", TEXT) == []


def test_version_problems_name_each_mismatch():
    [wrong_tag] = release.version_problems("v0.2.1", "0.3.0", TEXT)
    assert "v0.2.1" in wrong_tag and "0.3.0" in wrong_tag
    assert release.version_problems("0.3.0", "0.3.0", TEXT)  # tag without "v"
    [no_section] = release.version_problems("v0.4.0", "0.4.0", TEXT)
    assert "CHANGELOG" in no_section and "0.4.0" in no_section


def test_dist_problems_empty_for_a_clean_build(tmp_path):
    assert release.dist_problems(_dist(tmp_path), "0.2.0") == []


@pytest.mark.parametrize(
    "member",
    [
        "mifit-0.2.0/evidence/a.json",
        "mifit-0.2.0/work/x.bin",
        "mifit-0.2.0/reference/r.txt",
        "mifit-0.2.0/target/t.apk",
        "mifit-0.2.0/.mifit/session.json",
        "mifit-0.2.0/out.json.partial",
        "mifit-0.2.0/session.json",
        "mifit-0.2.0/.superpowers/ledger.md",
        "mifit-0.2.0/src/mifit/account.session.json",
        "mifit-0.2.0/secrets/key.txt",
        "mifit-0.2.0/private/notes.md",
    ],
)
def test_dist_problems_flag_private_paths(tmp_path, member):
    dist = _dist(tmp_path, sdist=DEFAULT_SDIST + (member,))

    [problem] = release.dist_problems(dist, "0.2.0")

    assert member in problem
    assert "mifit-0.2.0.tar.gz" in problem


def test_dist_problems_flag_private_paths_in_the_wheel(tmp_path):
    dist = _dist(tmp_path, wheel={**DEFAULT_WHEEL, "mifit/session.json": "{}"})

    [problem] = release.dist_problems(dist, "0.2.0")

    assert "mifit/session.json" in problem
    assert "mifit-0.2.0-py3-none-any.whl" in problem


def test_dist_problems_allow_session_in_a_test_name(tmp_path):
    dist = _dist(tmp_path, sdist=DEFAULT_SDIST + ("mifit-0.2.0/tests/test_session_store.py",))

    assert release.dist_problems(dist, "0.2.0") == []


def test_dist_problems_need_exactly_one_sdist_and_one_wheel(tmp_path):
    extra = _dist(tmp_path / "extra")
    (extra / "notes.txt").write_text("x", encoding="utf-8")
    [problem] = release.dist_problems(extra, "0.2.0")
    assert "notes.txt" in problem

    no_wheel = _dist(tmp_path / "no_wheel", make_wheel=False)
    [problem] = release.dist_problems(no_wheel, "0.2.0")
    assert "mifit-0.2.0-py3-none-any.whl" in problem

    other_version = _dist(tmp_path / "other", version="0.2.1")
    problems = release.dist_problems(other_version, "0.2.0")
    text = "\n".join(problems)
    assert "mifit-0.2.1.tar.gz" in text and "mifit-0.2.1-py3-none-any.whl" in text
    assert "mifit-0.2.0.tar.gz" in text and "mifit-0.2.0-py3-none-any.whl" in text


def test_dist_problems_need_py_typed_and_the_console_script(tmp_path):
    no_marker = {k: v for k, v in DEFAULT_WHEEL.items() if k != "mifit/py.typed"}
    [problem] = release.dist_problems(_dist(tmp_path / "a", wheel=no_marker), "0.2.0")
    assert "py.typed" in problem

    no_script = {**DEFAULT_WHEEL, "mifit-{v}.dist-info/entry_points.txt": "[console_scripts]\n"}
    [problem] = release.dist_problems(_dist(tmp_path / "b", wheel=no_script), "0.2.0")
    assert "mifit.cli:main" in problem


def test_main_against_this_repository(capsys):
    version = release.project_version(release.ROOT / "pyproject.toml")
    assert release.main(["version"]) == 0
    assert capsys.readouterr().out == f"{version}\n"
    assert release.main(["check-version", "--tag", f"v{version}"]) == 0
    assert release.main(["notes", version]) == 0
    assert capsys.readouterr().out.strip()  # this repo's own section is not empty
    assert release.main(["notes", "9.9.9"]) == 1
    assert "9.9.9" in capsys.readouterr().err


def test_main_check_dist_reports_problems_on_stderr(tmp_path, capsys):
    version = release.project_version(release.ROOT / "pyproject.toml")  # what check-dist expects
    dist = _dist(tmp_path, version=version, sdist=DEFAULT_SDIST + ("mifit-{v}/session.json",))

    assert release.main(["check-dist", str(dist)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "session.json" in err


SERVER = {
    "name": "io.github.Jenwein/mifit",
    "version": "0.3.0",
    "packages": [{"registryType": "pypi", "identifier": "mifit", "version": "0.3.0"}],
}
README = "# mifit\n\n<!-- mcp-name: io.github.Jenwein/mifit -->\n"


def _bundle_pyproject(version: str) -> str:
    return (
        f'[project]\nname = "mifit-claude-desktop"\nversion = "{version}"\n'
        f'dependencies = ["mifit=={version}"]\n'
    )


def _repo(
    root: Path, *, server_version: str = "0.3.0", bundle_version: str = "0.3.0"
) -> Path:
    """A minimal repository at ``root`` for ``check-version``: pyproject at 0.3.0,
    a CHANGELOG with a 0.3.0 section, the README marker, a ``server.json``
    carrying ``server_version`` in both places, and the bundle's manifest and
    pyproject at ``bundle_version``."""
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_bytes(b'[project]\nname = "mifit"\nversion = "0.3.0"\n')
    (root / "CHANGELOG.md").write_bytes(TEXT.encode("utf-8"))
    (root / "README.md").write_bytes(README.encode("utf-8"))
    server = {
        **SERVER,
        "version": server_version,
        "packages": [{**SERVER["packages"][0], "version": server_version}],
    }
    (root / "server.json").write_bytes(json.dumps(server).encode("utf-8"))
    bundle = root / "packaging" / "mcpb"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_bytes(json.dumps({"version": bundle_version}).encode("utf-8"))
    (bundle / "pyproject.toml").write_bytes(_bundle_pyproject(bundle_version).encode("utf-8"))
    return root


def test_server_json_problems_empty_when_versions_and_marker_agree():
    assert release.server_json_problems(SERVER, "0.3.0", README) == []


def test_server_json_problems_name_each_stale_version():
    stale = {
        **SERVER,
        "version": "0.2.0",
        "packages": [{**SERVER["packages"][0], "version": "0.2.0"}],
    }
    problems = release.server_json_problems(stale, "0.3.0", README)
    assert len(problems) == 2
    assert all("server.json" in p and "0.2.0" in p and "0.3.0" in p for p in problems)


@pytest.mark.parametrize(
    "readme",
    [
        "<!-- mcp-name: io.github.jenwein/mifit -->",  # the owner's case differs
        "mcp-name: io.github.Jenwein/mifit-pro",  # a longer name
        "mcp-name: io.github.Jenwein/mifit.",  # glued to a name character
        "mcp-name:io.github.Jenwein/mifit",  # no space after the colon
    ],
)
def test_server_json_problems_need_the_exact_marker(readme):
    [problem] = release.server_json_problems(SERVER, "0.3.0", readme)
    assert "README.md" in problem and "mcp-name: io.github.Jenwein/mifit" in problem


@pytest.mark.parametrize(
    "readme",
    [
        "<!-- mcp-name: io.github.Jenwein/mifit-->",  # comment close right after the name
        "mcp-name: io.github.Jenwein/mifit",  # end of text
        "x mcp-name: io.github.Jenwein/mifit-pro\nmcp-name: io.github.Jenwein/mifit\n",
    ],
)
def test_mcp_name_declared_accepts_the_registry_boundaries(readme):
    assert release.mcp_name_declared(readme, "io.github.Jenwein/mifit")


def test_main_check_version_names_a_stale_server_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path / "stale", server_version="0.2.0"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 1
    assert "server.json" in capsys.readouterr().err
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path / "ok"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 0


def test_bundle_source_problems_name_a_stale_manifest_or_pin():
    manifest = {"version": "0.3.0"}
    bundle = {"project": {"version": "0.3.0", "dependencies": ["mifit==0.3.0"]}}
    assert release.bundle_source_problems(manifest, bundle, "0.3.0") == []
    problems = release.bundle_source_problems(
        {"version": "0.2.0"},
        {"project": {"version": "0.2.0", "dependencies": ["mifit>=0.2"]}},
        "0.3.0",
    )
    assert len(problems) == 3
    assert "manifest.json" in problems[0] and "mifit==0.3.0" in problems[2]


def test_main_check_version_names_a_stale_bundle(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path / "stale", bundle_version="0.2.0"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 1
    assert "packaging/mcpb" in capsys.readouterr().err


BUNDLE_FILES = ("manifest.json", "pyproject.toml", "src/server.py")


def _bundle(
    tmp_path: Path,
    version: str,
    extra: tuple[str, ...] = (),
    manifest_version: str | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    """A real ``mifit-<version>.mcpb`` zip in ``tmp_path`` with the bundle's three
    files (less ``drop``) plus ``extra``; its manifest says ``manifest_version``
    (default ``version``)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"mifit-{version}.mcpb"
    manifest = json.dumps({"manifest_version": "0.4", "version": manifest_version or version})
    with zipfile.ZipFile(path, "w") as bundle:
        for name in (*BUNDLE_FILES, *extra):
            if name in drop:
                continue
            bundle.writestr(name, manifest if name == "manifest.json" else "")
    return path


def test_bundle_problems_empty_for_a_clean_bundle(tmp_path):
    assert release.bundle_problems(_bundle(tmp_path, "0.3.0"), "0.3.0") == []


@pytest.mark.parametrize(
    "extra",
    [".venv/pyvenv.cfg", "uv.lock", "session.json", "src/__pycache__/server.cpython-314.pyc"],
)
def test_bundle_problems_need_exactly_the_three_files(tmp_path, extra):
    [problem] = release.bundle_problems(_bundle(tmp_path, "0.3.0", extra=(extra,)), "0.3.0")
    assert extra in problem


def test_bundle_problems_name_a_missing_file_a_wrong_name_or_version(tmp_path):
    [missing] = release.bundle_problems(
        _bundle(tmp_path / "a", "0.3.0", drop=("src/server.py",)), "0.3.0"
    )
    assert "src/server.py" in missing

    misnamed = _bundle(tmp_path / "b", "0.2.0", manifest_version="0.3.0")
    [name] = release.bundle_problems(misnamed, "0.3.0")
    assert "mifit-0.2.0.mcpb" in name and "mifit-0.3.0.mcpb" in name

    stale = _bundle(tmp_path / "c", "0.3.0", manifest_version="0.2.0")
    [inner] = release.bundle_problems(stale, "0.3.0")
    assert "manifest.json" in inner and "0.2.0" in inner


def test_main_check_mcpb(tmp_path, capsys):
    version = release.project_version(release.ROOT / "pyproject.toml")  # what check-mcpb expects
    assert release.main(["check-mcpb", str(_bundle(tmp_path / "ok", version))]) == 0
    dirty = _bundle(tmp_path / "dirty", version, extra=("uv.lock",))
    assert release.main(["check-mcpb", str(dirty)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "uv.lock" in err
