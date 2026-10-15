# mifit Release to PyPI (R) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the already-verified mifit 0.2.0 to PyPI, so that `uv tool install mifit` / `pipx install mifit` / `pip install mifit` replace `git clone` + `pip install -e .`. Every release after it comes from a pushed `vX.Y.Z` tag through a token-free GitHub Actions pipeline and gets a GitHub Release.

**Architecture:**
- **Code and metadata.** Data behaviour does not change. `mifit.__version__` and `mifit --version` read the installed distribution's metadata. `pyproject.toml` gains the PyPI metadata and a `py.typed` marker. The README becomes PyPI-safe: absolute links, and PyPI install commands.
- **Release helper.** One stdlib-only helper, `tools/release.py`, holds the checks the pipeline runs. It checks that the tag, `pyproject` and CHANGELOG agree, checks what is inside the built sdist and wheel, and cuts the CHANGELOG section used as release notes. It is unit-tested offline.
- **Workflow.** `.github/workflows/release.yml` runs test → build → smoke (Linux, Windows, macOS) → publish. Publishing uses Trusted Publishing: a `pypi` job on a tag, or a `testpypi` job on a manual run. A GitHub Release job follows the PyPI publish. A structure test pins its permissions and triggers.

**Tech Stack:** Python 3.12+, setuptools ≥ 77 (PEP 639 license expression), `build` + `twine` (in CI and run locally via `uvx`), GitHub Actions, `pypa/gh-action-pypi-publish@release/v1`, PyYAML (test extra only, for the workflow structure test), pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-mifit-release-mcp-design.md`:
- §4 (R: the whole release design), §8 (version and CHANGELOG), §10 (what the user does);
- §11 D1–D4 (package name, release order, static version, no `authors`);
- §12 F1 (PyPI name facts) and F6 (Trusted Publishing facts).

Read §4 before starting.

**Precondition:**
- The branch `release-pypi` is cut from `main` after this plan's commit, and the tree is clean.
- `python -m pytest -q` gives 1251 passed, 2 skipped on the Windows dev machine. CI on Linux gives 1253 passed, because the 2 `time.tzset` tests run there.
- After the review, Tasks 1–5 are fast-forwarded into `main`. Task 6 is the live release with the user.

## Global Constraints

- **Name and version.** The distribution, import and command name are all `mifit`. The version is static in `pyproject.toml` and stays `0.2.0` (D3). There is no setuptools-scm.
- **Dependencies.** Runtime dependencies are unchanged: `requests>=2.31`, `qrcode>=7.4`, `cryptography>=42`, `requires-python = ">=3.12"`. The existing `test_pyproject_declares_package_dependencies_and_entry_point` must keep passing unchanged.
- **License and authors.**
  - `license = "MIT"` and `license-files = ["LICENSE", "THIRD_PARTY_NOTICES"]` stay as they are.
  - No `License ::` classifier (PEP 639 forbids mixing it with the license expression).
  - No `authors` field (D4).
- **Fixed names.** The workflow file is `.github/workflows/release.yml`, and the GitHub environments are `pypi` and `testpypi`. These are typed into PyPI's trusted-publisher form, so they must match exactly.
- **No credentials anywhere.**
  - No PyPI token in the repo or in GitHub Secrets.
  - Only the two publish jobs get `id-token: write`. Only `github-release` gets `contents: write`. The top level is `contents: read`.
  - Every `actions/checkout` uses `persist-credentials: false`.
  - The publish jobs never check out or run project code.
- **Build only in CI.** Nothing is uploaded from the dev machine: it holds git-ignored `evidence/`, `work/`, `reference/` and `target/` with real data. Local builds (Task 3) go to a scratch directory and are only inspected.
- **Tests stay offline.** `python -m pytest -q` passes after every task.
- **Public repo.** No health figure, account id or account date goes into a committed file. Task 6's comparison outputs go to the session scratchpad, never into the repo.
- **Line endings.** Every file is LF. Python's `write_text` on this machine writes CRLF, so normalize after any scripted edit. Before each commit, `git diff --check` must be clean and `git ls-files --eol <files>` must show `i/lf`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Ask the user before every push, tag push, workflow dispatch and publish.**
- **Doc languages.** The README, CHANGELOG, docstrings and workflow comments are in English; the roadmap is in Chinese.

## Review Focus

- **A tag that disagrees with the code.** Example: `v0.2.1` is pushed while `pyproject` still says `0.2.0`, or the CHANGELOG has no `## 0.2.1` section. The build job must fail before anything is published. → Task 2 `test_version_problems_name_each_mismatch`; Task 5 `test_build_job_checks_version_and_dist`.
- **A private file swept into the sdist** (`session.json`, `*.partial`, anything under `evidence/`, `work/`, `.mifit/`). The build must fail. A legitimate `tests/test_session_store.py` must not trip it. → Task 2 `test_dist_problems_flag_private_paths`, `test_dist_problems_allow_session_in_a_test_name`.
- **The PyPI project page.** Relative links and `#anchor` links in the README break there. → Task 4 `test_readme_links_are_absolute`.
- **A source checkout without an install.** `mifit --version` must print `mifit 0+unknown` instead of crashing. → Task 1 `test_installed_version_without_metadata_is_0_unknown`.
- **Re-running the TestPyPI dry run for the same version.** It must not fail on "file already exists". → Task 5 `test_testpypi_job_targets_testpypi_and_skips_existing`.

---

### Task 1: `mifit.__version__` and `mifit --version`

**Files:**
- Modify: `src/mifit/__init__.py`
- Modify: `src/mifit/cli/__init__.py` (`_build_parser`)
- Create: `tests/test_packaging.py`

**Interfaces:**
- Produces:
  - `mifit._installed_version(dist: str = "mifit") -> str`;
  - `mifit.__version__: str`;
  - the CLI flag `mifit --version`, which prints `mifit <version>\n` to stdout and exits 0.

- [ ] **Step 1: Write the failing tests** in the new `tests/test_packaging.py`. The module docstring is "Packaging: the version, the PyPI metadata and the README PyPI renders". It sets `ROOT = Path(__file__).resolve().parents[1]` and has a helper `_pyproject()` that loads `ROOT / "pyproject.toml"` with `tomllib`.

```python
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
```

- [ ] **Step 2: Run them; all three fail.** Run `python -m pytest tests/test_packaging.py -q`. Expected: `AttributeError` (no `__version__` / `_installed_version`) and a usage error for `--version`.

- [ ] **Step 3: Implement `_installed_version` and `__version__` in `src/mifit/__init__.py`.**
  - Call `importlib.metadata.version(dist)` through the module attribute, not `from importlib.metadata import version`, so the monkeypatch in the test reaches it.
  - Return `"0+unknown"` on `PackageNotFoundError`.
  - Set `__version__ = _installed_version()` after the imports.
  - Leave `__all__` unchanged.
  - Docstring: the version comes from the installed distribution's metadata, and the single source is `pyproject.toml`.

- [ ] **Step 4: Add the flag in `_build_parser`.** Add `parser.add_argument("--version", action="version", version=f"mifit {mifit.__version__}")`. Look up `__version__` through the `mifit` package already imported by the CLI module. Also add `--version` to the module docstring's list of commands and flags.

- [ ] **Step 5: Run `python -m pytest -q`.** Expected: 1254 passed, 2 skipped.

- [ ] **Step 6: Commit** `src/mifit/__init__.py`, `src/mifit/cli/__init__.py` and `tests/test_packaging.py` as `feat: mifit --version and mifit.__version__`.

---

### Task 2: `tools/release.py`, the release checks

**Files:**
- Create: `tools/release.py`. Stdlib only, so the build job runs it with a bare Python.
- Create: `tests/test_release_tool.py`.
- Modify: `tests/support.py`: add `load_release_tool() -> types.ModuleType`, which loads the script with `importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")` (`tools/` is not a package). Task 4's tests use it too.

**Interfaces:**
- Produces, all in `tools/release.py`:
  - `ROOT: Path`: the repository root, `Path(__file__).resolve().parents[1]`.
  - `project_version(pyproject: Path) -> str`: `[project].version`, read with `tomllib`.
  - `changelog_section(text: str, version: str) -> str | None`:
    - The body between the heading line that starts `## <version>` and the next line that starts `## `, or the end of the text. Stripped.
    - The heading must be `## <version>` followed by whitespace or the end of the line, so `0.2.0` matches neither `## 0.2.0.1` nor `## 0.2.01`.
    - `None` when there is no such heading.
  - `version_problems(tag: str, version: str, changelog: str) -> list[str]`:
    - Empty when `tag == f"v{version}"` and `changelog_section(changelog, version)` is a non-empty string.
    - Otherwise one message per mismatch. Each names the values it compared, e.g. `tag v0.2.1 does not match pyproject version 0.2.0`, or `CHANGELOG.md has no "## 0.2.0" section`.
  - `dist_problems(dist_dir: Path, version: str) -> list[str]`, the rules below.
  - `main(argv: list[str] | None = None) -> int`, with four subcommands. They read `ROOT / "pyproject.toml"` and `ROOT / "CHANGELOG.md"`; problems go to stderr as `error: <message>`, one per line.

    | Subcommand | Prints | Exit code |
    |---|---|---|
    | `version` | the project version and a newline | 0 |
    | `check-version --tag TAG` | problems only | 0 if none, else 1 |
    | `check-dist DIR` | problems only | 0 if none, else 1 |
    | `notes VERSION` | the CHANGELOG section | 0; 1 if the section is missing |

- **`dist_problems` rules:**
  - `dist_dir` holds exactly two files: `mifit-<version>.tar.gz` and `mifit-<version>-py3-none-any.whl`. Anything else is reported by name, and so is either file missing.
  - Members are read with `tarfile` (the sdist) and `zipfile` (the wheel). A member is reported (archive name plus member path) when any of these holds:
    - a path component is one of `evidence`, `work`, `reference`, `target`, `.mifit` or `.superpowers`;
    - the name ends with `.partial`;
    - the base name is `session.json`.
  - The wheel must contain `mifit/__init__.py` and `mifit/py.typed`. Its `mifit-<version>.dist-info/entry_points.txt` must contain the line `mifit = mifit.cli:main`.

- [ ] **Step 1: Write the failing tests.**
  - Helper `_dist(tmp_path, version="0.2.0", sdist=(...), wheel=(...))`. It writes a real `.tar.gz` and `.whl` whose members are the given paths with empty content. The default wheel also has `mifit/__init__.py`, `mifit/py.typed` and an `entry_points.txt` with `[console_scripts]\nmifit = mifit.cli:main\n`.
  - Synthetic CHANGELOG text:
    `"# Changelog\n\nintro\n\n## 0.3.0 — 2026-11-01\n\n### Added\n\n- b\n\n## 0.2.0 — 2026-10-15\n\n- a\n"`.

```python
def test_changelog_section_returns_the_body_up_to_the_next_heading():
    assert release.changelog_section(TEXT, "0.3.0") == "### Added\n\n- b"
    assert release.changelog_section(TEXT, "0.2.0") == "- a"   # last section: to the end
    assert release.changelog_section(TEXT, "0.4.0") is None

def test_changelog_section_needs_the_exact_version():
    text = "## 0.2.0.1 — x\n\n- no\n\n## 0.2.01\n\n- no\n"
    assert release.changelog_section(text, "0.2.0") is None

def test_version_problems_empty_when_tag_pyproject_and_changelog_agree():
    assert release.version_problems("v0.3.0", "0.3.0", TEXT) == []

def test_version_problems_name_each_mismatch():
    [wrong_tag] = release.version_problems("v0.2.1", "0.3.0", TEXT)
    assert "v0.2.1" in wrong_tag and "0.3.0" in wrong_tag
    assert release.version_problems("0.3.0", "0.3.0", TEXT)      # tag without "v"
    [no_section] = release.version_problems("v0.4.0", "0.4.0", TEXT)
    assert "CHANGELOG" in no_section and "0.4.0" in no_section

def test_dist_problems_empty_for_a_clean_build(tmp_path): ...       # == []

@pytest.mark.parametrize("member", [
    "mifit-0.2.0/evidence/a.json", "mifit-0.2.0/work/x.bin", "mifit-0.2.0/reference/r.txt",
    "mifit-0.2.0/target/t.apk", "mifit-0.2.0/.mifit/session.json", "mifit-0.2.0/out.json.partial",
    "mifit-0.2.0/session.json", "mifit-0.2.0/.superpowers/ledger.md",
])
def test_dist_problems_flag_private_paths(tmp_path, member):
    # put `member` in the sdist; exactly one problem, and it names `member`

def test_dist_problems_allow_session_in_a_test_name(tmp_path):
    # sdist member "mifit-0.2.0/tests/test_session_store.py" -> []

def test_dist_problems_need_exactly_one_sdist_and_one_wheel(tmp_path):
    # an extra "notes.txt" in dist_dir is reported; a missing wheel is reported;
    # files named for 0.2.1 while checking 0.2.0 are reported

def test_dist_problems_need_py_typed_and_the_console_script(tmp_path):
    # a wheel without mifit/py.typed -> a problem naming py.typed;
    # an entry_points.txt without the mifit line -> a problem naming mifit.cli:main

def test_main_against_this_repository(capsys):
    version = release.project_version(release.ROOT / "pyproject.toml")
    assert release.main(["version"]) == 0 and capsys.readouterr().out == f"{version}\n"
    assert release.main(["check-version", "--tag", f"v{version}"]) == 0
    assert release.main(["notes", version]) == 0
    assert capsys.readouterr().out.strip()              # this repo's own section is not empty
    assert release.main(["notes", "9.9.9"]) == 1
    assert "9.9.9" in capsys.readouterr().err
```

  The last test also guards the repository itself: `pyproject` and CHANGELOG must stay in step.

- [ ] **Step 2: Run them.** `python -m pytest tests/test_release_tool.py -q`. Expected: FAIL (no `tools/release.py`).

- [ ] **Step 3: Implement `tools/release.py`** to the interfaces above.
  - Use `argparse` with `prog="tools/release.py"`.
  - The module docstring names the workflow that runs it and states the four subcommands' contracts.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass. The count is the Task 1 count plus the new tests.

- [ ] **Step 5: Commit** `tools/release.py`, `tests/support.py` and `tests/test_release_tool.py` as `feat(release): version, dist and release-notes checks`.

---

### Task 3: PyPI metadata, `py.typed`, and a local build check

**Files:**
- Modify: `pyproject.toml`
- Create: `src/mifit/py.typed` (empty)
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: Task 2's `python tools/release.py check-dist DIR`.
- Produces: the metadata that Task 4's README links and Task 6's PyPI page rely on.

- [ ] **Step 1: Write the failing tests** in `tests/test_packaging.py`:

```python
REPO = "https://github.com/Jenwein/mifit"

def test_pyproject_has_the_pypi_metadata():
    project = _pyproject()["project"]
    assert project["readme"] == "README.md"
    assert project["urls"] == {
        "Homepage": REPO, "Source": REPO, "Issues": f"{REPO}/issues",
        "Changelog": f"{REPO}/blob/main/CHANGELOG.md",
    }
    assert {
        "Development Status :: 3 - Alpha", "Environment :: Console",
        "Operating System :: OS Independent", "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.12", "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14", "Typing :: Typed",
    } <= set(project["classifiers"])
    assert not [c for c in project["classifiers"] if c.startswith("License ::")]
    assert "authors" not in project
    assert project["keywords"]


def test_py_typed_ships_with_the_package():
    assert (ROOT / "src" / "mifit" / "py.typed").is_file()
    assert "py.typed" in _pyproject()["tool"]["setuptools"]["package-data"]["mifit"]
```

- [ ] **Step 2: Run them.** Expected: FAIL (`KeyError: 'readme'`).

- [ ] **Step 3: Edit `pyproject.toml`.**
  - Add `readme`, `keywords`, `classifiers` and `[project.urls]` as tested.
  - The full classifier list is the tested set plus `Programming Language :: Python :: 3`, `Intended Audience :: Developers`, `Intended Audience :: End Users/Desktop` and `Topic :: Utilities`.
  - `keywords = ["mi-fitness", "xiaomi", "health", "fitness", "wearable", "sleep", "heart-rate"]`.
  - Add `[tool.setuptools.package-data]` with `mifit = ["py.typed"]`.
  - Create the empty `src/mifit/py.typed`.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Build locally into the scratchpad and check the result.** `$SCRATCH` is the session scratchpad. `dist` must be a fresh, empty directory.

```bash
uvx --from build pyproject-build --outdir "$SCRATCH/dist"
uvx twine check --strict "$SCRATCH"/dist/*
python tools/release.py check-dist "$SCRATCH/dist"
uv venv "$SCRATCH/venv312" --python 3.12
uv pip install --python "$SCRATCH/venv312" "$SCRATCH"/dist/mifit-0.2.0-py3-none-any.whl
"$SCRATCH/venv312/Scripts/mifit" --version
"$SCRATCH/venv312/Scripts/python" -c "import mifit, pathlib; print(pathlib.Path(mifit.__file__).with_name('py.typed').is_file())"
```

  Expected:
  - `twine` prints `PASSED` for both files;
  - `check-dist` prints nothing and exits 0;
  - `mifit --version` prints `mifit 0.2.0`;
  - the last command prints `True`.

  Also list the sdist with `tar -tzf`. It should hold `pyproject.toml`, `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES`, `PKG-INFO`, `src/mifit/**` and `src/mifit.egg-info/**`, and nothing from `notes/`, `docs/`, `tools/` or the git-ignored directories. If it holds more, find out why before going on.

- [ ] **Step 6: Commit** `pyproject.toml`, `src/mifit/py.typed` and `tests/test_packaging.py` as `build: PyPI metadata and py.typed`. Nothing from the scratchpad is committed.

---

### Task 4: README, CHANGELOG and roadmap for the release

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/superpowers/roadmap.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: Task 3's URLs (the base for links is `https://github.com/Jenwein/mifit`).

- [ ] **Step 1: Write the failing tests** in `tests/test_packaging.py`:

```python
def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")

def _section(text: str, heading: str) -> str:
    # the lines after "## <heading>" up to the next "## " line

def test_readme_links_are_absolute():
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
    section = release.changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), "0.2.0")
    assert "### Packaging" in section and "uv tool install mifit" in section
```

  In the last test, `release = load_release_tool()` (from `tests/support.py`, Task 2).

- [ ] **Step 2: Run them.** Expected: FAIL.
  - The relative links found are: `#disclaimer`, `CHANGELOG.md`, `#known-limitations`, `LICENSE` (twice) and `THIRD_PARTY_NOTICES`.
  - There is no PyPI install and no badge.

- [ ] **Step 3: Edit `README.md`.**
  - **Badge.** Add `[![PyPI](https://img.shields.io/pypi/v/mifit)](https://pypi.org/project/mifit/)` next to the CI badge.
  - **Links.** Rewrite every relative link to an absolute GitHub URL:
    - files become `https://github.com/Jenwein/mifit/blob/main/<path>`;
    - in-page anchors become `https://github.com/Jenwein/mifit#<anchor>`.
  - **Paths that are only code text** become links as well:
    - `notes/domain-catalog.md`, at its three mentions;
    - `notes/08` and `notes/11`, pointing to `notes/08-heart-rate-keys-and-completeness.md` and `notes/11-spo2-weight-profile.md`.
  - **"## Install"** becomes:
    - one line: "Requires Python 3.12 or later; `uv` installs one for you if needed";
    - then the three commands from spec §4.4, each with its one-line comment translated to English;
    - then "Upgrade: `uv tool upgrade mifit` (or `pipx upgrade mifit`, `pip install -U mifit`)."
  - **"## Development"** starts with the `git clone` + `cd mifit` lines, before its existing `pip install -e ".[test]"`.

- [ ] **Step 4: Add the Packaging subsection to `CHANGELOG.md`.** At the end of the 0.2.0 section (after "### Fixed"), add:

```markdown
### Packaging

- Published on PyPI as `mifit`: install with `uv tool install mifit`,
  `pipx install mifit` or `pip install mifit` instead of cloning the
  repository.
- `mifit --version` and `mifit.__version__`.
- The package is marked as typed (`py.typed`).
```

  Leave the 0.2.0 date as it is. Task 6 changes it if the release happens on a later day.

- [ ] **Step 5: Update `docs/superpowers/roadmap.md`, in Chinese.**
  - In §0's 横向工作 table, add the rows:
    - `R 发布到 PyPI`: status "实现完成，待首次发布（见计划 2026-10-15-mifit-release-pypi）";
    - `P MCP 服务`: status "设计完成，待计划";
    - `E 生态登记`: status "设计完成，待计划".
  - Add one line under §1, pointing to the spec `specs/2026-10-15-mifit-release-mcp-design.md`.
  - Bump the "更新" date only if it is not already 2026-10-15.

- [ ] **Step 6: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 7: Commit** `README.md`, `CHANGELOG.md`, `docs/superpowers/roadmap.md` and `tests/test_packaging.py` as `docs: README, CHANGELOG and roadmap for the PyPI release`.

---

### Task 5: `.github/workflows/release.yml`

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `tests/test_release_workflow.py`
- Modify: `pyproject.toml`: add `"pyyaml"` to the `test` extra, and update its comment to say what PyYAML is for.

**Interfaces:**
- Consumes: Task 2's `python tools/release.py version | check-version --tag TAG | check-dist DIR | notes VERSION`.
- Produces: the job names and environment names that Task 6's PyPI and GitHub settings must match.

**The workflow:**
- **Triggers:** `push` with `tags: ["v*"]`, and `workflow_dispatch` with no inputs. Top-level `permissions: contents: read`.
- **Shell:** `defaults.run.shell: bash` (Windows smoke steps need bash for globs).
- **Action versions:** the same majors as `ci.yml` for `actions/checkout` and `actions/setup-python` (`@v7` today). `actions/upload-artifact@v5` and `actions/download-artifact@v6` (PyPA guide, spec §12 F6), or newer majors if they exist when this is implemented. Every checkout has `with: persist-credentials: false`.

| Job | Runs on | `needs` / `if` | Steps |
|---|---|---|---|
| `test` | ubuntu-latest | — | Matrix Python `["3.12", "3.14"]`: checkout; setup-python; `python -m pip install -e ".[test]"`; `python -m pytest -q -rs` |
| `build` | ubuntu-latest | — | 1. checkout<br>2. setup-python 3.12<br>3. step `meta`: `echo "version=$(python tools/release.py version)" >> "$GITHUB_OUTPUT"`<br>4. `if: github.ref_type == 'tag'`: `python tools/release.py check-version --tag "$GITHUB_REF_NAME"`<br>5. `python -m pip install build twine`<br>6. `python -m build`<br>7. `twine check --strict dist/*`<br>8. `python tools/release.py check-dist dist`<br>9. upload `dist/` as artifact `dist`<br>Job output: `version` |
| `smoke` | matrix `os: [ubuntu-latest, windows-latest, macos-latest]` | `needs: build` | No checkout. setup-python 3.12; download artifact `dist`; `python -m pip install dist/*.whl`; `test "$(mifit --version)" = "mifit ${{ needs.build.outputs.version }}"`; `mifit --help`; `python -c "import mifit; assert mifit.__version__ == '${{ needs.build.outputs.version }}'"` |
| `publish-pypi` | ubuntu-latest | `needs: [test, build, smoke]`<br>`if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')`<br>`environment: {name: pypi, url: https://pypi.org/p/mifit}`<br>`permissions: id-token: write` | download artifact `dist` into `dist/`; `pypa/gh-action-pypi-publish@release/v1` with defaults (attestations stay on) |
| `publish-testpypi` | ubuntu-latest | `needs: [test, build, smoke]`<br>`if: github.event_name == 'workflow_dispatch'`<br>`environment: {name: testpypi, url: https://test.pypi.org/p/mifit}`<br>`permissions: id-token: write` | download artifact; the same action `with: repository-url: https://test.pypi.org/legacy/` and `skip-existing: true` |
| `github-release` | ubuntu-latest | `needs: [build, publish-pypi]`<br>`if:` as `publish-pypi`<br>`permissions: contents: write` | 1. checkout (for CHANGELOG and the tool)<br>2. setup-python 3.12<br>3. download artifact `dist`<br>4. `python tools/release.py notes "${{ needs.build.outputs.version }}" > notes.md`<br>5. `gh release create "$GITHUB_REF_NAME" dist/* --title "$GITHUB_REF_NAME" --notes-file notes.md --verify-tag` with `GH_TOKEN: ${{ github.token }}` |

A comment at the top of the file says three things: what triggers each path; that PyPI's trusted publishers must name this file and the `pypi` / `testpypi` environments; and that nothing here holds a token.

- [ ] **Step 1: Write the failing tests** in `tests/test_release_workflow.py`.
  - Load the file with `yaml.safe_load`.
  - PyYAML reads the key `on` as `True` (YAML 1.1). Use `wf.get("on", wf.get(True))`.
  - Helpers:
    - `_steps(job)`;
    - `_uses(job)`, the list of `uses` values;
    - `_runs(job)`, all `run` strings joined;
    - `_env_name(job)`, which accepts a string or a `{name: ...}` mapping.

```python
def test_triggers_are_version_tags_and_manual_runs_only():
    on = _on()
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"] == {"tags": ["v*"]}

def test_default_permissions_are_read_only():
    assert WF["permissions"] == {"contents": "read"}

def test_jobs():
    assert set(JOBS) == {"test", "build", "smoke", "publish-pypi", "publish-testpypi", "github-release"}

def test_only_the_publish_jobs_can_mint_an_oidc_token():
    minted = {n for n, j in JOBS.items() if j.get("permissions", {}).get("id-token") == "write"}
    assert minted == {"publish-pypi", "publish-testpypi"}
    assert JOBS["publish-pypi"]["permissions"] == {"id-token": "write"}

def test_publish_jobs_run_no_project_code():
    for name in ("publish-pypi", "publish-testpypi"):
        assert not [u for u in _uses(JOBS[name]) if u.startswith("actions/checkout")]
        assert "pypa/gh-action-pypi-publish@release/v1" in _uses(JOBS[name])
        assert set(JOBS[name]["needs"]) == {"test", "build", "smoke"}

def test_pypi_job_runs_on_version_tags_in_the_pypi_environment():
    job = JOBS["publish-pypi"]
    assert _env_name(job) == "pypi"
    assert "refs/tags/v" in job["if"] and "push" in job["if"]

def test_testpypi_job_targets_testpypi_and_skips_existing():
    job = JOBS["publish-testpypi"]
    assert _env_name(job) == "testpypi" and "workflow_dispatch" in job["if"]
    [publish] = [s for s in _steps(job) if s.get("uses", "").startswith("pypa/")]
    assert publish["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert publish["with"]["skip-existing"] is True

def test_build_job_checks_version_and_dist():
    runs = _runs(JOBS["build"])
    assert "tools/release.py check-version --tag" in runs
    assert "twine check --strict" in runs and "tools/release.py check-dist dist" in runs
    [check] = [s for s in _steps(JOBS["build"]) if "check-version" in s.get("run", "")]
    assert "tag" in check["if"]

def test_smoke_installs_the_built_wheel_on_three_systems_without_checkout():
    job = JOBS["smoke"]
    assert set(job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest", "macos-latest"}
    assert not [u for u in _uses(job) if u.startswith("actions/checkout")]
    assert "mifit --version" in _runs(job)

def test_github_release_follows_the_pypi_publish():
    job = JOBS["github-release"]
    assert job["permissions"] == {"contents": "write"}
    assert "publish-pypi" in job["needs"] and job["if"] == JOBS["publish-pypi"]["if"]
    assert "tools/release.py notes" in _runs(job) and "gh release create" in _runs(job)

def test_every_checkout_drops_its_credentials():
    for name, job in JOBS.items():
        for step in _steps(job):
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False, name
```

- [ ] **Step 2: Install PyYAML and run the tests.** Add `"pyyaml"` to the `test` extra, then `python -m pip install -e ".[test]"`. Run `python -m pytest tests/test_release_workflow.py -q`. Expected: FAIL (no workflow file).

- [ ] **Step 3: Write `.github/workflows/release.yml`** as tabled above.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass. Then run actionlint on the file (`uvx --from actionlint-py actionlint .github/workflows/release.yml`, or a local binary) and fix what it reports. If it cannot be run, say so in the review.

- [ ] **Step 5: Commit** `.github/workflows/release.yml`, `tests/test_release_workflow.py` and `pyproject.toml` as `ci: release workflow with PyPI trusted publishing`.

- [ ] **Step 6: Final review and merge.** One fresh reviewer reads the whole branch against spec §4 and this plan. Fix the findings. Then fast-forward `release-pypi` into `main`, and ask the user before pushing.

---

### Task 6: First release, 0.2.0 (live, with the user)

There is no new code here; every outward step waits for the user's go-ahead. Record each step's outcome in this plan's checkboxes. Record the numbers each step produces in the scratchpad only.

- [ ] **Step 1 (me, after the user agrees): push `main`.**
  - The `CI` workflow must be green on the pushed commit.
  - `release.yml` does not run: it has no `push` trigger for branches.

- [ ] **Step 2 (user): PyPI and TestPyPI accounts with 2FA.** Skip this if they already exist.

- [ ] **Step 3 (user): add a pending trusted publisher on each index.**

  | Index | Page | Project | Owner | Repository | Workflow | Environment |
  |---|---|---|---|---|---|---|
  | PyPI | <https://pypi.org/manage/account/publishing/> | `mifit` | `Jenwein` | `mifit` | `release.yml` | `pypi` |
  | TestPyPI | <https://test.pypi.org/manage/account/publishing/> | `mifit` | `Jenwein` | `mifit` | `release.yml` | `testpypi` |

  A pending publisher does not reserve the name (spec §12 F1), so do Steps 3–7 on the same day if possible. If PyPI refuses the name `mifit`, stop and ask the user: the fallback (spec D1) renames only the distribution, to `mifit-cloud`, and that touches Tasks 2–5.

- [ ] **Step 4 (user): create the GitHub environments.** Under GitHub → `Jenwein/mifit` → Settings → Environments, create `pypi` and `testpypi`. Recommended: on `pypi`, add "Required reviewers" with the user as the reviewer.

- [ ] **Step 5 (me, after the user agrees): TestPyPI dry run.** Run `gh workflow run release.yml --ref main` and watch it with `gh run watch`.
  - Expected: `test`, `build`, `smoke` (all three systems) and `publish-testpypi` are green; `publish-pypi` and `github-release` are skipped.
  - Then install from TestPyPI without its dependencies, which are taken from PyPI to avoid look-alike packages on TestPyPI:

```bash
uv venv "$SCRATCH/testpypi"
uv pip install --python "$SCRATCH/testpypi" --no-deps --index-url https://test.pypi.org/simple/ mifit==0.2.0
uv pip install --python "$SCRATCH/testpypi" "requests>=2.31" "qrcode>=7.4" "cryptography>=42"
"$SCRATCH/testpypi/Scripts/mifit" --version
```

  Expected: `mifit 0.2.0`. Check that <https://test.pypi.org/project/mifit/> renders the README, with the badge, links and install section.

- [ ] **Step 6 (me): the CHANGELOG date.** If today is not 2026-10-15, change `## 0.2.0 — 2026-10-15` to today's date. Commit as `docs: 0.2.0 release date` and push it (with the user's go-ahead).

- [ ] **Step 7 (me, after the user agrees): tag and push.**

```bash
git tag -a v0.2.0 -m "mifit 0.2.0"
git push origin v0.2.0
```

  The user approves the `pypi` environment deployment in GitHub. Expected:
  - every job is green except `publish-testpypi`, which is skipped;
  - <https://pypi.org/project/mifit/0.2.0/> exists, with attestations shown for both files;
  - the GitHub Release `v0.2.0` holds both files, and its body is the 0.2.0 CHANGELOG section.

- [ ] **Step 8 (me): check the install from PyPI on this machine.**
  - Run `uv tool install mifit`, then `mifit --version`, which should print `mifit 0.2.0`.
  - Run one export with the user's existing session, `mifit export steps --from <yesterday> --to <yesterday> --json "$SCRATCH/pypi-steps.json"`, and do the same from the source checkout (`python -m mifit.cli export ...`).
  - The two files' `records` must be equal. Compare them by script and report only "equal" or the differing field names, never the figures.
  - Afterwards, `uv tool uninstall mifit` if the user does not want to keep it.

- [ ] **Step 9 (me): close out.**
  - Roadmap: R becomes "✅ 0.2.0 已发布到 PyPI（<date>）".
  - The README is already correct.
  - Memory: update the project memory with "released 0.2.0 to PyPI; trusted publishers configured; next is P (MCP)".
  - Commit, and push with the user's go-ahead.
