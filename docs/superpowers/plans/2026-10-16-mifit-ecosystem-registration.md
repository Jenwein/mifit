# mifit Ecosystem Registration (E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship mifit 0.3.1, which is listed in the official MCP Registry as `io.github.Jenwein/mifit` and comes with a one-click Claude Desktop extension (`mifit-0.3.1.mcpb`, uv type) on its GitHub Release.

**Architecture:**
- **No library change.** E adds metadata and release plumbing around the 0.3.0 server:
  - `server.json` at the repository root (registry metadata);
  - `packaging/mcpb/` (bundle sources: `manifest.json`, a `pyproject.toml` that pins `mifit==<version>`, and a five-line entry script);
  - two new jobs in `.github/workflows/release.yml`: `mcpb` builds, checks and smoke-runs the bundle; `publish-mcp-registry` runs `mcp-publisher` after the PyPI publish.
- **One version, six places.** `tools/release.py check-version` grows to cover them all (pyproject, CHANGELOG, both `server.json` versions, the bundle manifest and the bundle's pin), and the README marker must name exactly the `server.json` name.
- **The bundle is tried before it is shipped.** A bundle pinned to `mifit==0.3.0`, which is already on PyPI, goes into the user's Claude Desktop before any bundle job is added to the release workflow (D16 gate, Task 4).

**Tech Stack:** Python 3.12+ (stdlib only in `tools/`), pytest + PyYAML (existing `test` extra), GitHub Actions, `mcp-publisher` v1.8.1 (pinned binary), `@anthropic-ai/mcpb` 2.1.2 (via `npx`), uv (Claude Desktop's runtime for the bundle; `pip install uv` in CI).

**Spec:** `docs/superpowers/specs/2026-10-15-mifit-release-mcp-design.md`:
- §6 (E: client configs, MCP Registry, `.mcpb`);
- §8 (version 0.3.x, CHANGELOG), §11 D16;
- §12 F4/F5/F7 (rechecked below), §13 (the owner's case).

Read §6 before starting.

## Facts rechecked on 2026-10-16 (spec §12 F4/F5/F7, §13)

Checked against the primary sources: the registry repository at tag v1.8.1 and `main`, its docs (`docs/modelcontextprotocol-io/*.mdx`, `docs/reference/cli/commands.md`), the published schema, the mcpb repository (`MANIFEST.md`, `schemas/`, `src/shared/config.ts`, `examples/hello-world-uv`, issues), and npm.

- **Registry schema.** `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` is still the newest; v1.8.1 and `main` embed no later one.
  - Required: `name` (pattern `^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`), `description` (1–100 characters), `version` (an exact version; ranges are rejected). `title` ≤ 100 characters.
  - A package requires `registryType`, `identifier` and `transport`. A PyPI package needs `version` and must not have `fileSha256`.
  - `packageArguments` items: `{"type": "positional", "value": ...}` (a positional needs `value` or `valueHint`) or `{"type": "named", "name": "--flag", ...}`.
  - `repository` requires `url` and `source`.
  - The registry also accepts `registryType: "mcpb"` (GitHub/GitLab release URL + `fileSha256`). This plan does not use it (E-D2).
- **mcp-publisher.** v1.8.1 (2026-08-06) is still the latest release. The archive `mcp-publisher_linux_amd64.tar.gz` (7.3 MB) has the SHA-256 `a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc`; the Windows one (7.5 MB) has `399ad0d6e00a50812b563a71d8bfbff5160c085e6b13aac6ec083d98d5ff7c45` (`registry_1.8.1_checksums.txt`).
  - CLI: `mcp-publisher validate [file]` (schema and semantic checks; it asks the registry's `/v0/validate` endpoint, so it needs the network but no token, and package ownership is checked only on publish — corrected in the final review), `mcp-publisher login github-oidc` (needs `id-token: write`), `mcp-publisher publish [file]` (default `./server.json`).
  - An outdated binary fails with `invalid audience`, so the pin must be the newest release when it goes in (Task 2 checks).
  - A version the registry already holds cannot be published again. A just-published PyPI version can briefly be missing from PyPI's JSON API; the registry then says "version … was not found … retry".
- **The owner's case (§13).** `io.github.Jenwein/mifit`, with a capital J.
  - `login github-oidc` grants `io.github.<repository_owner>/*` with the owner exactly as GitHub spells it (`github_oidc.go`), and `isResourceMatch` (`internal/auth/jwt.go`) is a case-sensitive prefix match. A lowercase name gets 403. This is registry issue #689, still open; the maintainers said "use the same casing".
  - The PyPI ownership check (`containsMCPNameToken`) is case-sensitive too: `mcp-name: <name>` followed by a boundary (end, a character outside `[A-Za-z0-9._/-]`, or `-->` / `--!>`).
  - So the 0.3.0 README on PyPI, whose marker says `io.github.jenwein/mifit`, can back no registry entry. E needs a new PyPI version: 0.3.1.
  - `gh api users/Jenwein` → login `Jenwein`; the repository is `Jenwein/mifit`.
  - The registry has no `mifit` entry yet.
- **MCPB.** Manifest 0.4 is still the newest, and `mcpb-manifest-latest.schema.json` still pins 0.3, so the manifest names 0.4 explicitly. `@anthropic-ai/mcpb` 2.1.2 is still `latest` on npm (CLI: `mcpb validate <path>`, `mcpb pack <dir> [output]`).
  - The 0.4 schema requires `name`, `version`, `description`, `author` (`name`) and `server`. `server` requires `type`, `entry_point` and `mcp_config` (with `command`), uv type included (mcpb issue #263, open).
  - **`user_config` supports `boolean`** (types: string, number, boolean, directory, file). In `mcp_config`, `${user_config.KEY}` for a boolean becomes the string `"true"` or `"false"` (`src/shared/config.ts`).
  - uv type: the bundle holds `manifest.json` and `pyproject.toml`, and the host installs the dependencies with uv. The official example runs `uv run --directory ${__dirname} src/server.py`.
  - Still open: #297 (Claude Desktop does not pass `mcp_config.env` to the server) and #278 (Claude Desktop rejects signed bundles). #291 (uv type) was closed on 2026-07-23 as fixed in Claude Desktop.
- **F7, client configs.** Unchanged since P put them in the README (0.3.0). E adds no client.

## Decisions this plan adds to the spec

Each can be overruled. They are listed here so the reviewer can check them.

- **E-D1: the name is `io.github.Jenwein/mifit`, and E ships 0.3.1.**
  - The registry's case rules (above) leave no other way.
  - The README marker becomes `<!-- mcp-name: io.github.Jenwein/mifit -->`.
  - 0.3.0 is never registered.
  - This fills §8's "0.3.x" and answers §13.
- **E-D2: `server.json` lists only the PyPI package.**
  - Listing the `.mcpb` too (`registryType: "mcpb"`) would need its SHA-256 written into `server.json` by the release job after packing. The uv bundle only wraps the PyPI package.
  - Later, if a client wants it.
- **E-D3: the registry job runs no project code.**
  - It downloads `server.json` from the `build` job's artifact; there is no checkout. It downloads the pinned `mcp-publisher` and checks its SHA-256.
  - Then: up to 6 tries of `login github-oidc` + `publish`, 20 s apart. The tries cover PyPI's listing lag; each try gets a fresh token. (`validate` runs earlier, in the `build` job, before anything is published: final-review fix.)
  - It has no `environment`: the registry's OIDC check ignores it, and an environment without rules protects nothing.
- **E-D4: no registry dry run.**
  - `workflow_dispatch` (the TestPyPI dry run) never touches the registry: the official registry has no test namespace, and a registered version is final.
  - `mcp-publisher validate` runs locally in Task 1, and in the `build` job on every run (the dry run included), before any publish job starts.
- **E-D5: the GPS switch reaches mifit as an argument, not an environment variable** (#297).
  - `mcp_config.args` ends with `${user_config.allow_gps}`. The entry script turns on `--allow-gps` only for exactly `"true"`; anything else keeps GPS off (fail closed).
  - The default is `false`, so §5.5's two gates still hold: the switch, then the tool's `include_track`.
- **E-D6: the manifest's author is `mifit contributors`**, with the repository URL. The schema requires an author, and D4 left personal attribution to the user.
- **E-D7: the bundle is built in CI on every release run and attached only on tags.**
  - The `mcpb` job packs with a pinned `mcpb`. It checks that the bundle holds exactly three files. It then starts the server from the unpacked bundle with `uv run … --find-links dist/`, against the wheel just built, and lists its tools.
  - `publish-pypi` and `publish-testpypi` wait for `mcpb`: a broken bundle stops the release before anything is published.
  - `tools/mcp_handshake.py` is the shared handshake. The `smoke` job keeps its inline copy because it runs without a checkout.
- **E-D8: the D16 check comes before the bundle is wired into the release** (Task 4).
  - The bundle built in Task 3 pins `mifit==0.3.0`, which is on PyPI, so the user can install it in Claude Desktop at once.
  - If it does not install or start, Task 5 is skipped. The README and CHANGELOG then leave the bundle out, and the sources stay in `packaging/mcpb/` for a later try.

## Global Constraints

- **Version.** 0.3.1 in all six places at once (Task 6):
  - `pyproject.toml` `[project].version`;
  - a `## 0.3.1 — <date>` CHANGELOG section;
  - `server.json` `version` and `packages[0].version`;
  - `packaging/mcpb/manifest.json` `version`;
  - `packaging/mcpb/pyproject.toml` `version` and its one dependency, `mifit==0.3.1`.

  Until Task 6, all of them say 0.3.0.
- **Registry metadata** (`server.json`), exactly:
  - `$schema` `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`;
  - `name` `io.github.Jenwein/mifit`;
  - one package: `registryType` `pypi`, `identifier` `mifit`, `runtimeHint` `uvx`, `transport` `{"type": "stdio"}`, `packageArguments` `[{"type": "positional", "value": "mcp"}]`.
  - No `--allow-gps` anywhere in it: GPS stays a manual opt-in (D13).
- **mcp-publisher.** v1.8.1 only, from `https://github.com/modelcontextprotocol/registry/releases/download/v1.8.1/…`, SHA-256 checked. Never `releases/latest`.
- **MCPB.**
  - `manifest_version` `"0.4"`, `server.type` `"uv"`;
  - packed with `npx --yes @anthropic-ai/mcpb@2.1.2` (pinned);
  - not signed (#278); no `env` in `mcp_config` (#297).
  - The bundle holds exactly `manifest.json`, `pyproject.toml` and `src/server.py`.
  - Nothing under `packaging/` has an `__init__.py`, so the folder can never shadow the `packaging` library.
- **The release workflow.**
  - Jobs and permissions stay as they are except where a task says otherwise.
  - Only the three publish jobs (`publish-pypi`, `publish-testpypi`, `publish-mcp-registry`) can mint an OIDC token, and none of them checks out the repository.
  - Every checkout keeps `persist-credentials: false`.
  - The actions keep their current majors (`checkout@v7`, `setup-python@v7`, `upload-artifact@v7`, `download-artifact@v8`); the new `setup-node` is `@v7`.
- **`tools/release.py`** stays standard library only: the workflow runs it before anything is installed.
- **Tests stay offline.** `python -m pytest -q` passes after every task. The only subprocess servers are local (`python -m mifit.cli mcp`, as `tests/test_mcp_cli.py` already does).
- **Downloads.** The local checks in Tasks 1 and 3 fetch `mcp-publisher_windows_amd64.tar.gz` (7.5 MB, from the registry's v1.8.1 GitHub release) and `@anthropic-ai/mcpb@2.1.2` (npm, about 1 MB unpacked). Ask the user once before the first one. Each goes into its own new folder in the session scratchpad, never into the repository.
- **Public repo.** No health figure or account data in a committed file. Live outputs go to the scratchpad only.
- **Line endings.** Every file is LF. Python's `write_text` on this machine writes CRLF, so normalize after any scripted edit. Before each commit, `git diff --check` must be clean, and `git ls-files --eol <files>` must show `i/lf`.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Ask the user before every push, workflow dispatch and tag push.** A `v*` tag push publishes to PyPI and now to the MCP Registry at once: the `pypi` environment has no reviewer, and a registry version cannot be withdrawn.
- **Languages.** README, CHANGELOG, `server.json`, the manifest and docstrings are in English; the roadmap is in Chinese.

**Precondition:**
- The branch `ecosystem` is cut from `main` after this plan's commit, in its own worktree, `C:\dev\mifitness-re-worktrees\ecosystem`. The main checkout is shared with other sessions, so never switch branches there.
- The worktree gets its own `.venv` with `python -m pip install -e ".[test]"`, as P's did.
- Baseline: `python -m pytest -q` gives 1476 passed, 2 skipped on the Windows dev machine (Python 3.14).
- After the final review (Task 6), the branch is fast-forwarded into `main`. Task 7 is the release, with the user.

## Review Focus

- **The GitHub owner's case.** A name spelled `io.github.jenwein/…` passes every local check, then gets 403 at the very end of a release, after PyPI has the version. The name must be derived from the repository owner exactly, and the README marker must match it character for character. → Task 1 `test_server_name_uses_the_github_owner_exactly`, `test_server_json_problems_need_the_exact_marker`.
- **The registry job runs seconds after the PyPI upload**, when PyPI may not list 0.3.1 yet. It must retry instead of failing the release. → Task 2 `test_registry_publish_retries_while_pypi_catches_up`.
- **The GPS switch arrives as anything but `"true"`**: unset, `"True"`, `"1"`, or the unreplaced `${user_config.allow_gps}`. GPS must stay off. → Task 3 `test_entry_turns_on_gps_only_for_exactly_true`.
- **A `.venv`, `uv.lock` or `session.json` next to the bundle sources when packing** (anyone who tried the bundle locally has one). The shipped bundle must hold exactly its three files. → Task 3 `test_bundle_problems_need_exactly_the_three_files`.
- **A version bump that misses one of the six places.** The tag build must stop before anything is published, and name the file. → Task 1 `test_main_check_version_names_a_stale_server_json`; Task 3 `test_main_check_version_names_a_stale_bundle`; the repository test `test_main_against_this_repository`.

---

## Part 1: the registry

### Task 1: `server.json`, the README marker and their checks

**Files:**
- Create: `server.json`
- Modify: `README.md` (the marker line only)
- Modify: `tools/release.py` (module docstring, new functions, `check-version`)
- Create: `tests/test_server_json.py`
- Modify: `tests/test_release_tool.py`, `tests/test_packaging.py` (`MCP_NAME_MARKER`)

**Interfaces:**
- Produces, in `tools/release.py`:
  - `SERVER_JSON_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"`
  - `mcp_name_declared(readme: str, name: str) -> bool`: the registry's own rule (`containsMCPNameToken`, v1.8.1). Some occurrence of `"mcp-name: " + name` is followed by the end of the text, by a character outside `[A-Za-z0-9._/-]`, or by `-->` / `--!>`. The comparison is case-sensitive.
  - `server_json_problems(server: dict, version: str, readme: str) -> list[str]`, with one problem for each of:
    - `server["version"] != version`;
    - each package whose `version != version`;
    - `not mcp_name_declared(readme, server["name"])`.

    Each message names `server.json` (or `README.md`), the value found and the value expected.
  - `check-version` now also reads `ROOT / "server.json"` and `ROOT / "README.md"`, and reports `server_json_problems` after `version_problems`. `main` reads `ROOT` when it is called, so tests can point it at a temporary repository with `monkeypatch.setattr(release, "ROOT", tmp)`. No other module constant may be derived from `ROOT`.
- Produces, in `tests/test_release_tool.py`: `_repo(root: Path, *, server_version: str = "0.3.0") -> Path`. It creates `root` and writes a minimal repository there: `pyproject.toml` at 0.3.0, a CHANGELOG with a non-empty `## 0.3.0` section, a README with the marker, and `server.json` with `server_version` in both places. It returns `root`. Task 3 adds `bundle_version`.

`server.json` (2-space indent, LF, final newline):

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.Jenwein/mifit",
  "title": "mifit",
  "description": "Read-only access to your own Mi Fitness (Xiaomi) data: sleep, heart rate, steps, workouts",
  "version": "0.3.0",
  "repository": {"url": "https://github.com/Jenwein/mifit", "source": "github"},
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "mifit",
      "version": "0.3.0",
      "runtimeHint": "uvx",
      "transport": {"type": "stdio"},
      "packageArguments": [{"type": "positional", "value": "mcp"}]
    }
  ]
}
```

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_server_json.py` (loads `server.json` and `pyproject.toml` from the repository root):

```python
def test_server_json_uses_the_current_registry_schema():
    assert SERVER["$schema"] == release.SERVER_JSON_SCHEMA

def test_server_name_uses_the_github_owner_exactly():
    # The registry grants io.github.<owner>/* with GitHub's spelling and compares
    # case-sensitively (registry issue #689): "jenwein" would get 403.
    owner = PYPROJECT["project"]["urls"]["Source"].split("/")[3]
    assert owner == "Jenwein"
    assert SERVER["name"] == f"io.github.{owner}/mifit"

def test_server_json_fields():
    assert set(SERVER) == {"$schema", "name", "title", "description", "version", "repository", "packages"}
    assert 1 <= len(SERVER["description"]) <= 100 and 1 <= len(SERVER["title"]) <= 100
    assert SERVER["repository"] == {"url": "https://github.com/Jenwein/mifit", "source": "github"}
    assert SERVER["packages"] == [{
        "registryType": "pypi", "identifier": "mifit", "version": SERVER["version"],
        "runtimeHint": "uvx", "transport": {"type": "stdio"},
        "packageArguments": [{"type": "positional", "value": "mcp"}],
    }]

def test_server_json_never_turns_on_gps():
    assert "--allow-gps" not in (ROOT / "server.json").read_text(encoding="utf-8")
```

  - `tests/test_release_tool.py`:

```python
SERVER = {"name": "io.github.Jenwein/mifit", "version": "0.3.0",
          "packages": [{"registryType": "pypi", "identifier": "mifit", "version": "0.3.0"}]}
README = "# mifit\n\n<!-- mcp-name: io.github.Jenwein/mifit -->\n"

def test_server_json_problems_empty_when_versions_and_marker_agree():
    assert release.server_json_problems(SERVER, "0.3.0", README) == []

def test_server_json_problems_name_each_stale_version():
    stale = {**SERVER, "version": "0.2.0", "packages": [{**SERVER["packages"][0], "version": "0.2.0"}]}
    problems = release.server_json_problems(stale, "0.3.0", README)
    assert len(problems) == 2
    assert all("server.json" in p and "0.2.0" in p and "0.3.0" in p for p in problems)

@pytest.mark.parametrize("readme", [
    "<!-- mcp-name: io.github.jenwein/mifit -->",   # the owner's case differs
    "mcp-name: io.github.Jenwein/mifit-pro",         # a longer name
    "mcp-name: io.github.Jenwein/mifit.",            # glued to a name character
    "mcp-name:io.github.Jenwein/mifit",              # no space after the colon
])
def test_server_json_problems_need_the_exact_marker(readme):
    [problem] = release.server_json_problems(SERVER, "0.3.0", readme)
    assert "README.md" in problem and "mcp-name: io.github.Jenwein/mifit" in problem

@pytest.mark.parametrize("readme", [
    "<!-- mcp-name: io.github.Jenwein/mifit-->",     # comment close right after the name
    "mcp-name: io.github.Jenwein/mifit",             # end of text
    "x mcp-name: io.github.Jenwein/mifit-pro\nmcp-name: io.github.Jenwein/mifit\n",
])
def test_mcp_name_declared_accepts_the_registry_boundaries(readme):
    assert release.mcp_name_declared(readme, "io.github.Jenwein/mifit")

def test_main_check_version_names_a_stale_server_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path / "stale", server_version="0.2.0"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 1
    assert "server.json" in capsys.readouterr().err
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path / "ok"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 0
```

  - `tests/test_packaging.py`: `MCP_NAME_MARKER = "<!-- mcp-name: io.github.Jenwein/mifit -->"`. In `test_readme_carries_the_mcp_registry_marker`, also assert that `"io.github.jenwein"` is not in the README.

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_server_json.py tests/test_release_tool.py tests/test_packaging.py -q`. Expected: `server.json` is missing, `release` has no `server_json_problems`, and the README marker test fails.

- [ ] **Step 3: Implement.**
  - Add `server.json` as above.
  - Change the README marker line.
  - In `tools/release.py`:
    - add the constant and the two functions;
    - wire them into `check-version`;
    - the module docstring's `check-version` line now says: "the tag must be `v<version>`, the CHANGELOG must have a non-empty `## <version>` section, `server.json` must carry `<version>` twice, and the README must declare `mcp-name: <its name>`".

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass. `test_main_against_this_repository` now also proves that this repository's `server.json` and README agree with `pyproject`.

- [ ] **Step 5: Validate with the registry's own tool.** With the user's OK for the download (Global Constraints):
  - Download `https://github.com/modelcontextprotocol/registry/releases/download/v1.8.1/mcp-publisher_windows_amd64.tar.gz` into a new, empty folder in the scratchpad.
  - Check its SHA-256 against `399ad0d6e00a50812b563a71d8bfbff5160c085e6b13aac6ec083d98d5ff7c45`.
  - Extract it there (`tar -xzf`), and run `mcp-publisher.exe validate <worktree>\server.json`.
  - Expected: `✅ server.json is valid`. Any warning goes into the task report.

- [ ] **Step 6: Commit** `server.json`, `README.md`, `tools/release.py`, `tests/test_server_json.py`, `tests/test_release_tool.py` and `tests/test_packaging.py` as `feat(registry): server.json and the version and marker checks`.

---

### Task 2: The registry job in the release workflow

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: `server.json` (Task 1). The `build` job's `check-version` step, which on a tag now also checks `server.json`.
- Produces:
  - In the `build` job, after "Check the contents": an `actions/upload-artifact@v7` step with `name: server-json`, `path: server.json` and `if-no-files-found: error`.
  - A new job `publish-mcp-registry`:
    - name "Publish to the MCP Registry";
    - `needs: [build, publish-pypi]`, `if:` the `TAG_PUSH` expression;
    - `runs-on: ubuntu-latest`, `timeout-minutes: 10`;
    - `permissions: {id-token: write}`; no checkout, no `environment`.

    Its steps:
    1. `actions/download-artifact@v8` of `server-json` into `registry/`.
    2. "Install mcp-publisher v1.8.1":
       - `curl -sSfL -o mcp-publisher.tar.gz` from the v1.8.1 `mcp-publisher_linux_amd64.tar.gz` URL;
       - `echo "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc  mcp-publisher.tar.gz" | sha256sum --check`;
       - `tar -xzf mcp-publisher.tar.gz mcp-publisher`;
       - `./mcp-publisher validate registry/server.json`.
    3. "Publish": up to 6 tries, 20 s apart (E-D3):

```bash
# The registry reads the version from PyPI, which can take a moment to list a
# version it has just accepted. Each try logs in again for a fresh token.
for attempt in 1 2 3 4 5 6; do
  if ./mcp-publisher login github-oidc && ./mcp-publisher publish registry/server.json; then
    exit 0
  fi
  echo "Try $attempt failed; trying again in 20 s." >&2
  sleep 20
done
exit 1
```

  - The workflow's header comment gains one line: a tag push also publishes `server.json` to the MCP Registry, after PyPI, with the job's OIDC token and no stored secret.

- [ ] **Step 1: Write the failing tests** in `tests/test_release_workflow.py`:
  - `test_jobs`: the set gains `"publish-mcp-registry"`.
  - `test_only_the_publish_jobs_can_mint_an_oidc_token`: `minted == {"publish-pypi", "publish-testpypi", "publish-mcp-registry"}`, and `JOBS["publish-mcp-registry"]["permissions"] == {"id-token": "write"}`.
  - New:

```python
MCP_PUBLISHER = ("https://github.com/modelcontextprotocol/registry/releases/download/"
                 "v1.8.1/mcp-publisher_linux_amd64.tar.gz")
MCP_PUBLISHER_SHA256 = "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc"

def test_build_job_uploads_the_dists_and_server_json():
    uploads = {s["with"]["name"]: s["with"]["path"] for s in _steps(JOBS["build"])
               if s.get("uses", "").startswith("actions/upload-artifact")}
    assert uploads == {"dist": "dist/", "server-json": "server.json"}

def test_registry_job_follows_the_pypi_publish_on_tags():
    job = JOBS["publish-mcp-registry"]
    assert job["if"] == TAG_PUSH and set(job["needs"]) == {"build", "publish-pypi"}

def test_registry_job_runs_a_pinned_checked_publisher_on_the_built_server_json():
    job = JOBS["publish-mcp-registry"]
    assert not [u for u in _uses(job) if u.startswith("actions/checkout")]
    downloads = [s for s in _steps(job) if s.get("uses", "").startswith("actions/download-artifact")]
    assert [d["with"]["name"] for d in downloads] == ["server-json"]
    runs = _runs(job)
    assert MCP_PUBLISHER in runs and MCP_PUBLISHER_SHA256 in runs and "sha256sum --check" in runs
    assert "releases/latest" not in runs
    assert "mcp-publisher validate registry/server.json" in runs
    assert "mcp-publisher login github-oidc" in runs
    assert "mcp-publisher publish registry/server.json" in runs

def test_registry_publish_retries_while_pypi_catches_up():
    [step] = [s for s in _steps(JOBS["publish-mcp-registry"]) if "publish registry/" in s.get("run", "")]
    run = step["run"]
    assert "for attempt in" in run and "sleep 20" in run
    assert run.index("for attempt in") < run.index("login github-oidc")   # a fresh token per try
```

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_release_workflow.py -q`.

- [ ] **Step 3: Check the pin is current.** `gh api repos/modelcontextprotocol/registry/releases/latest --jq .tag_name` must still say `v1.8.1`. If it says something newer, stop and ask the user: the pin, the hash and the tests then change together.

- [ ] **Step 4: Implement** the upload step, the job and the header line.

- [ ] **Step 5: Run `python -m pytest -q`.** Expected: all pass. Also run `python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" .github/workflows/release.yml` (a parse check of the edited file).

- [ ] **Step 6: Commit** `.github/workflows/release.yml` and `tests/test_release_workflow.py` as `ci(release): publish server.json to the MCP Registry after PyPI`.

---

## Part 2: the Claude Desktop bundle

### Task 3: Bundle sources, their checks and the handshake tool

**Files:**
- Create: `packaging/mcpb/manifest.json`, `packaging/mcpb/pyproject.toml`, `packaging/mcpb/src/server.py`
- Create: `tools/mcp_handshake.py`
- Modify: `tools/release.py` (docstring, new functions, `check-version`, a new subcommand `check-mcpb`)
- Modify: `tests/support.py`: add `load_script(path: Path, name: str) -> ModuleType`, and make `load_release_tool()` call it.
- Create: `tests/test_mcpb_bundle.py`
- Modify: `tests/test_release_tool.py` (`_repo` writes the bundle files too)

**Interfaces:**
- Consumes: `release.main`, `check-version` and the `_repo` helper from Task 1; `P2_TOOLS` from `tests/mcp_fakes.py`.
- Produces:
  - In `packaging/mcpb/src/server.py`:
    - `mifit_args(argv: list[str]) -> list[str]` returns `["mcp", "--allow-gps"]` when `argv == ["true"]`, and `["mcp"]` otherwise.
    - Under `if __name__ == "__main__":` it runs `sys.exit(main(mifit_args(sys.argv[1:])))`, where `main` is `from mifit.cli import main`.
    - The module docstring says that Claude Desktop runs this file with uv (`manifest.json`), and that the extension's "Allow GPS tracks" switch arrives as the one argument, `"true"` or `"false"`.
  - In `tools/release.py`:
    - `BUNDLE_DIR = Path("packaging", "mcpb")`, relative: `main` reads `ROOT / BUNDLE_DIR` (Task 1's rule), and `BUNDLE_MEMBERS = frozenset({"manifest.json", "pyproject.toml", "src/server.py"})`.
    - `bundle_source_problems(manifest: dict, bundle_pyproject: dict, version: str) -> list[str]`, with one problem for each of:
      - `manifest["version"] != version`;
      - `bundle_pyproject["project"]["version"] != version`;
      - `bundle_pyproject["project"]["dependencies"] != [f"mifit=={version}"]`.

      Each message names its file (`packaging/mcpb/manifest.json` or `packaging/mcpb/pyproject.toml`).
    - `check-version` also reports `bundle_source_problems` for the two files under `ROOT`.
    - `bundle_problems(bundle: Path, version: str) -> list[str]`:
      - the file name must be `mifit-<version>.mcpb`;
      - the zip members must be exactly `BUNDLE_MEMBERS`, with one problem per missing and per extra member;
      - the inner `manifest.json` `version` must be `version`.
    - Subcommand `check-mcpb PATH` runs `_report(bundle_problems(PATH, version))`. The module docstring lists it.
  - In `tools/mcp_handshake.py` (stdlib only):
    - `handshake(command: list[str], timeout_s: float = 120) -> set[str]` starts `command` and talks to it like a client. Its body is the `smoke` job's inline script, turned into a function:
      - it sends `initialize`, `notifications/initialized` and `tools/list` as JSON lines, and waits for each reply before sending the next;
      - `encoding="utf-8"`, a watchdog `threading.Timer(timeout_s, proc.kill)`, and `communicate()` to close stdin.
      - It returns the tool names. Every failure becomes a `RuntimeError` naming the problem, with the server's stderr: a non-zero exit, stray stdout, a missing or non-JSON reply, and an `OSError` such as a broken pipe from a server that has already exited.
    - `main(argv: list[str] | None = None) -> int` takes `argv` (default `sys.argv[1:]`) verbatim as the command, with no option parsing: `--directory` and the like belong to the command. It prints `MCP server answered: <n> tools` and returns 0 when `get_status` is among them; otherwise, including on a `RuntimeError`, it prints `error: <message>` to stderr and returns 1.

`packaging/mcpb/manifest.json` (2-space indent, LF):

```json
{
  "manifest_version": "0.4",
  "name": "mifit",
  "display_name": "mifit (Mi Fitness data)",
  "version": "0.3.0",
  "description": "Your own Mi Fitness (Xiaomi) health data for Claude: sleep, heart rate, steps, workouts. Read-only.",
  "long_description": "…",
  "author": {"name": "mifit contributors", "url": "https://github.com/Jenwein/mifit"},
  "repository": {"type": "git", "url": "https://github.com/Jenwein/mifit"},
  "homepage": "https://github.com/Jenwein/mifit",
  "support": "https://github.com/Jenwein/mifit/issues",
  "license": "MIT",
  "keywords": ["mi-fitness", "xiaomi", "health", "sleep", "heart-rate"],
  "server": {
    "type": "uv",
    "entry_point": "src/server.py",
    "mcp_config": {
      "command": "uv",
      "args": ["run", "--directory", "${__dirname}", "src/server.py", "${user_config.allow_gps}"]
    }
  },
  "user_config": {
    "allow_gps": {
      "type": "boolean",
      "title": "Allow GPS tracks",
      "description": "…",
      "default": false,
      "required": false
    }
  },
  "tools": [{"name": "get_status", "description": "…"}],
  "compatibility": {"platforms": ["darwin", "linux", "win32"], "runtimes": {"python": ">=3.12"}}
}
```

The three "…" fields:
- `long_description`, in short sentences:
  - unofficial (not affiliated with Xiaomi);
  - the tools' results go to Claude's model provider, like the rest of the chat;
  - the login stays in `~/.mifit` on this computer, and Claude can log you in with a QR code (`login_start`);
  - GPS routes are off unless the switch is on, and a route shows where you were, including where you live.
- `user_config.allow_gps.description` says: let Claude read a workout's GPS route when you ask for it; a route shows where you were, including where you live; off by default.
- `tools` lists all 16 tools, in the README table's order. Each description is the README table's "What it reads" text.

`packaging/mcpb/pyproject.toml`:

```toml
# Claude Desktop installs this bundle's one dependency with uv (manifest.json,
# server.type "uv"). There is no build system: the bundle itself is not a package.
[project]
name = "mifit-claude-desktop"
version = "0.3.0"
requires-python = ">=3.12"
dependencies = ["mifit==0.3.0"]
```

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_mcpb_bundle.py`:

```python
def test_manifest_is_a_0_4_uv_bundle_that_runs_the_entry_with_the_switch():
    assert MANIFEST["manifest_version"] == "0.4"
    assert MANIFEST["server"] == {
        "type": "uv", "entry_point": "src/server.py",
        "mcp_config": {"command": "uv", "args": [
            "run", "--directory", "${__dirname}", "src/server.py", "${user_config.allow_gps}"]},
    }   # no "env": Claude Desktop does not pass it on (mcpb #297)

def test_gps_switch_is_off_by_default():
    option = MANIFEST["user_config"]["allow_gps"]
    assert set(MANIFEST["user_config"]) == {"allow_gps"}
    assert (option["type"], option["title"], option["default"], option["required"]) == (
        "boolean", "Allow GPS tracks", False, False)
    assert "where you live" in option["description"]

def test_manifest_lists_the_servers_tools():
    names = [t["name"] for t in MANIFEST["tools"]]
    assert len(names) == len(set(names)) and set(names) == P2_TOOLS
    assert all(t["description"] for t in MANIFEST["tools"])

def test_manifest_metadata():
    assert MANIFEST["name"] == "mifit" and MANIFEST["license"] == "MIT"
    assert MANIFEST["author"] == {"name": "mifit contributors", "url": REPO}
    assert MANIFEST["repository"] == {"type": "git", "url": REPO}
    assert MANIFEST["compatibility"] == {"platforms": ["darwin", "linux", "win32"],
                                         "runtimes": {"python": ">=3.12"}}
    assert "signature" not in MANIFEST     # Claude Desktop rejects signed bundles (mcpb #278)

def test_bundle_pyproject_pins_this_release_and_builds_nothing():
    bundle = tomllib.loads((BUNDLE / "pyproject.toml").read_text(encoding="utf-8"))
    version = PYPROJECT["project"]["version"]
    assert bundle["project"]["dependencies"] == [f"mifit=={version}"]
    assert bundle["project"]["requires-python"] == ">=3.12" and "build-system" not in bundle

@pytest.mark.parametrize("argv", [[], ["false"], ["True"], ["1"], ["${user_config.allow_gps}"], ["true", "x"]])
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
    assert not list((ROOT / "packaging").rglob("__init__.py"))

def test_handshake_lists_this_servers_tools():
    assert HANDSHAKE.handshake([sys.executable, "-m", "mifit.cli", "mcp"]) == P2_TOOLS

def test_handshake_fails_on_a_server_that_exits():
    assert HANDSHAKE.main([sys.executable, "-c", "pass"]) == 1
```

  The module's constants:
  - `ROOT` is the repository root, `BUNDLE = ROOT / "packaging" / "mcpb"` and `REPO = "https://github.com/Jenwein/mifit"`;
  - `MANIFEST` and `PYPROJECT` are the parsed `packaging/mcpb/manifest.json` and the root `pyproject.toml`;
  - `ENTRY = load_script(BUNDLE / "src" / "server.py", "mcpb_entry")`;
  - `HANDSHAKE = load_script(ROOT / "tools" / "mcp_handshake.py", "mcp_handshake")`.

  - `tests/test_release_tool.py`:
    - `_repo` also writes `packaging/mcpb/manifest.json` and `packaging/mcpb/pyproject.toml`, at a `bundle_version` that defaults to 0.3.0.
    - Add:

```python
def test_bundle_source_problems_name_a_stale_manifest_or_pin():
    manifest, bundle = {"version": "0.3.0"}, {"project": {"version": "0.3.0", "dependencies": ["mifit==0.3.0"]}}
    assert release.bundle_source_problems(manifest, bundle, "0.3.0") == []
    problems = release.bundle_source_problems({"version": "0.2.0"},
        {"project": {"version": "0.2.0", "dependencies": ["mifit>=0.2"]}}, "0.3.0")
    assert len(problems) == 3 and "manifest.json" in problems[0] and "mifit==0.3.0" in problems[2]

def test_main_check_version_names_a_stale_bundle(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", _repo(tmp_path, bundle_version="0.2.0"))
    assert release.main(["check-version", "--tag", "v0.3.0"]) == 1
    assert "packaging/mcpb" in capsys.readouterr().err

def test_bundle_problems_empty_for_a_clean_bundle(tmp_path):
    assert release.bundle_problems(_bundle(tmp_path, "0.3.0"), "0.3.0") == []

@pytest.mark.parametrize("extra", [".venv/pyvenv.cfg", "uv.lock", "session.json", "src/__pycache__/server.cpython-314.pyc"])
def test_bundle_problems_need_exactly_the_three_files(tmp_path, extra):
    [problem] = release.bundle_problems(_bundle(tmp_path, "0.3.0", extra=(extra,)), "0.3.0")
    assert extra in problem

def test_bundle_problems_name_a_missing_file_a_wrong_name_or_version(tmp_path): ...
    # no src/server.py -> one problem naming it; mifit-0.2.0.mcpb checked as 0.3.0 -> the name;
    # an inner manifest saying 0.2.0 -> the version

def test_main_check_mcpb(tmp_path, capsys): ...    # a clean bundle -> 0; one with uv.lock -> 1, "error: " on stderr
```

  (`_bundle(tmp_path, version, extra=(), manifest_version=None, drop=())` writes a real zip named `mifit-<version>.mcpb`.)

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_mcpb_bundle.py tests/test_release_tool.py -q`.

- [ ] **Step 3: Implement** the three bundle files, `tools/mcp_handshake.py`, the `tools/release.py` changes and `load_script`.

- [ ] **Step 4: Run `python -m pytest -q`.** Expected: all pass.

- [ ] **Step 5: Pack and run the bundle locally.** With the user's OK for the npm download (Global Constraints). `$SCRATCH` is a new folder in the session scratchpad.
  - `npx --yes @anthropic-ai/mcpb@2.1.2 validate packaging/mcpb/manifest.json`. Expected: the manifest is valid.
  - `npx --yes @anthropic-ai/mcpb@2.1.2 pack packaging/mcpb "$SCRATCH/mifit-0.3.0.mcpb"`.
  - `python tools/release.py check-mcpb "$SCRATCH/mifit-0.3.0.mcpb"`. Expected: exit 0. If `mcpb pack` adds or renames a member, stop and report the member list: `BUNDLE_MEMBERS` and this plan then need a decision, not a quiet change.
  - `python -m zipfile -e "$SCRATCH/mifit-0.3.0.mcpb" "$SCRATCH/bundle"`, then `python tools/mcp_handshake.py uv run --directory "$SCRATCH/bundle" src/server.py false`.
    - This resolves `mifit==0.3.0` from PyPI.
    - Expected: `MCP server answered: 16 tools`.
  - Keep `$SCRATCH/mifit-0.3.0.mcpb` for Task 4.

- [ ] **Step 6: Commit** the bundle files, `tools/mcp_handshake.py`, `tools/release.py`, `tests/support.py`, `tests/test_mcpb_bundle.py` and `tests/test_release_tool.py` as `feat(mcpb): Claude Desktop bundle sources and their checks`.

---

### Task 4: The bundle in Claude Desktop (with the user): the D16 gate

There is no code here. Do it as soon as Task 3 is done. Tasks 5 and 6 depend on its answer, so wait for it.

- [ ] **Step 1 (user, with my help): install `mifit-0.3.0.mcpb` from Task 3 in Claude Desktop on Windows.**
  - If Claude Desktop already has a `mifit` server from the JSON config, the user turns it off first, so the two do not clash.
  - The user opens the file (double-click, or Settings → Extensions → install from file).
  - Check, and note in the scratchpad (no health figures):
    1. the install dialog shows mifit, its tools and the "Allow GPS tracks" switch, off;
    2. after install, `get_status` answers and says GPS is off (the existing `~/.mifit` session is used);
    3. one everyday question works, for example "我昨天走了多少步";
    4. asking for a workout's route is refused while the switch is off;
    5. with the switch on (and the extension restarted, if Claude Desktop asks), `get_status` says GPS is allowed. Then switch it off again.
- [ ] **Step 2: Decide.**
  - **All five pass:** Task 5 next.
  - **Otherwise** (it does not install, does not start, or the switch does not reach the server): D16 applies.
    - Write down what failed in the scratchpad and in Task 6's roadmap line.
    - Skip Task 5. Task 6 leaves the bundle out of the README and CHANGELOG.
    - `packaging/mcpb/` stays, still kept at the release version by `check-version`, for a later try.
  - Either way, the user removes or keeps the test extension as they like. It pins 0.3.0, so after the release it should be replaced by the 0.3.1 bundle (Task 7).

---

### Task 5: The bundle in the release workflow (only if Task 4 passed)

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: `tools/release.py check-mcpb`, `tools/mcp_handshake.py` (Task 3); the `build` job's `version` output and its `dist` artifact.
- Produces:
  - A new job `mcpb`:
    - name "Claude Desktop bundle";
    - `needs: build`; runs on every trigger;
    - `runs-on: ubuntu-latest`, `timeout-minutes: 10`; default permissions.

    Its steps, with `V=${{ needs.build.outputs.version }}`:
    1. `actions/checkout@v7` with `persist-credentials: false`; `actions/setup-python@v7` with 3.12; `actions/setup-node@v7` with `node-version: "24"`.
    2. `actions/download-artifact@v8` of `dist` into `dist/`.
    3. "Pack": `npx --yes @anthropic-ai/mcpb@2.1.2 validate packaging/mcpb/manifest.json`, then `npx --yes @anthropic-ai/mcpb@2.1.2 pack packaging/mcpb "mifit-$V.mcpb"`.
    4. "Check the contents": `python tools/release.py check-mcpb "mifit-$V.mcpb"`.
    5. "The bundle starts the server with uv":
       - `python -m pip install uv`;
       - `python -m zipfile -e "mifit-$V.mcpb" bundle`;
       - `python tools/mcp_handshake.py uv run --directory "$PWD/bundle" --find-links "$PWD/dist" src/server.py false`.

       This uses the wheel just built: on a tag, PyPI does not have it yet.
    6. `actions/upload-artifact@v7`: `name: mcpb`, `path: mifit-*.mcpb`, `if-no-files-found: error`.
  - `publish-pypi` and `publish-testpypi`: `needs: [test, build, smoke, mcpb]`.
  - `github-release`:
    - `needs: [build, publish-pypi, mcpb]`;
    - it also downloads `mcpb` into `bundle/`;
    - `gh release create "$GITHUB_REF_NAME" dist/* bundle/*.mcpb …`.
  - The header comment's line about the GitHub Release now names the Claude Desktop bundle too.

- [ ] **Step 1: Write the failing tests** in `tests/test_release_workflow.py`:
  - `test_jobs`: the set gains `"mcpb"`.
  - `test_publish_jobs_run_no_project_code`: `set(JOBS[name]["needs"]) == {"test", "build", "smoke", "mcpb"}`.
  - New:

```python
def test_mcpb_job_packs_checks_and_runs_the_bundle_with_pinned_tools():
    job = JOBS["mcpb"]
    assert job["needs"] in ("build", ["build"]) and "if" not in job and "permissions" not in job
    runs = _runs(job)
    assert "npx --yes @anthropic-ai/mcpb@2.1.2 pack packaging/mcpb" in runs
    assert "npx --yes @anthropic-ai/mcpb@2.1.2 validate packaging/mcpb/manifest.json" in runs
    assert "tools/release.py check-mcpb" in runs
    assert "tools/mcp_handshake.py uv run" in runs and '--find-links "$PWD/dist"' in runs
    assert "actions/setup-node@v7" in _uses(job)
    uploads = [s["with"]["name"] for s in _steps(job) if s.get("uses", "").startswith("actions/upload-artifact")]
    assert uploads == ["mcpb"]

def test_a_broken_bundle_stops_every_publish():
    for name in ("publish-pypi", "publish-testpypi"):
        assert "mcpb" in JOBS[name]["needs"], name
```

  - `test_github_release_follows_the_pypi_publish`: also assert `"mcpb" in job["needs"]` and `"bundle/*.mcpb" in _runs(job)`.

- [ ] **Step 2: Run them; they fail.** `python -m pytest tests/test_release_workflow.py -q`.

- [ ] **Step 3: Implement** the job and the three job changes.

- [ ] **Step 4: Run `python -m pytest -q`** and the YAML parse check from Task 2 Step 4. Expected: all pass.

- [ ] **Step 5: Commit** as `ci(release): build, check and attach the Claude Desktop bundle`.

---

## Part 3: 0.3.1

### Task 6: README, CHANGELOG, version 0.3.1 and roadmap; final review

**Files:**
- Modify: `README.md` (the MCP section), `CHANGELOG.md`, `pyproject.toml` (`version = "0.3.1"`), `server.json` (both versions), `packaging/mcpb/manifest.json` (`version`), `packaging/mcpb/pyproject.toml` (`version` and the pin), `docs/superpowers/roadmap.md`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `check-version` (Tasks 1 and 3). Through `test_main_against_this_repository`, it fails until all six places say 0.3.1, so bump them in one step.

- [ ] **Step 1: Write the failing tests** in `tests/test_packaging.py`:
  - Rename `test_version_is_0_3_0_with_its_changelog` to `test_version_is_0_3_1_with_its_changelog`. It asserts:
    - version `"0.3.1"`;
    - the 0.3.1 notes contain `"MCP Registry"` and `"io.github.Jenwein/mifit"`;
    - if Task 5 was done, also `".mcpb"` and `"Allow GPS tracks"`.
  - New `test_readme_points_to_the_registry_and_the_bundle`:
    - the MCP section contains `"io.github.Jenwein/mifit"`;
    - if Task 5 was done, also `"https://github.com/Jenwein/mifit/releases/latest"`, `".mcpb"` and `"Allow GPS tracks"`.
  - `test_readme_links_are_absolute` must keep passing.

- [ ] **Step 2: Run them; they fail.**

- [ ] **Step 3: Edit the README's "Use with an AI assistant (MCP)" section.**
  - If Task 5 was done: after the paragraph that opens with **Add the server to your assistant.**, add one paragraph headed **Claude Desktop, in one click.** It says:
    - download `mifit-<version>.mcpb` from the [latest release](https://github.com/Jenwein/mifit/releases/latest) and open it, and Claude Desktop (Windows, macOS) offers to install the extension;
    - its settings have an **Allow GPS tracks** switch, off by default;
    - use either the extension or the JSON config, not both.
  - Always: one sentence after the config snippets. It says mifit is listed in the official [MCP Registry](https://modelcontextprotocol.io/registry/about) as `io.github.Jenwein/mifit`, for clients that install servers from it. Open the link once to check that it works; if it does not, use `https://github.com/modelcontextprotocol/registry`.

- [ ] **Step 4: Add the CHANGELOG `## 0.3.1 — 2026-10-16` section** above 0.3.0. Task 7 sets the real date.
  - **Added:**
    - mifit is listed in the official MCP Registry as `io.github.Jenwein/mifit`; the release workflow publishes `server.json` there after PyPI.
    - If Task 5 was done: `mifit-<version>.mcpb`, a Claude Desktop extension on each GitHub Release, with an "Allow GPS tracks" switch that is off by default.
  - **Fixed:** the README's registry marker now spells the GitHub owner as the registry checks it (`Jenwein`); the registry compares names case-sensitively, so 0.3.0 could not be listed.

- [ ] **Step 5: Bump the version** in `pyproject.toml`, `server.json` (both), `packaging/mcpb/manifest.json` and `packaging/mcpb/pyproject.toml` (version and `mifit==0.3.1`).

- [ ] **Step 6: Roadmap (Chinese).** The E row becomes "实现完成，待发布 0.3.1（计划 [2026-10-16-mifit-ecosystem-registration](plans/2026-10-16-mifit-ecosystem-registration.md)）". If D16 applied, add "；`.mcpb` 推迟：<Task 4 的原因>".

- [ ] **Step 7: Run `python -m pytest -q`.** Expected: all pass. Then:
  - `python tools/release.py check-version --tag v0.3.1`. Expected: exit 0.
  - Rebuild locally into the scratchpad as P Task 10 Step 6 did: `uvx --from build pyproject-build --outdir "$SCRATCH/dist"`, `uvx twine check --strict "$SCRATCH/dist/*"`, `python tools/release.py check-dist "$SCRATCH/dist"`.
  - Run `mcp-publisher.exe validate server.json` again (Task 1's download).

- [ ] **Step 8: Commit** as `docs: README registry and bundle, CHANGELOG and version 0.3.1`.

- [ ] **Step 9: Final review and merge.**
  - One fresh reviewer, on the most capable model, reads the whole branch against spec §6, §8 and D16, this plan's "Facts rechecked" and "Decisions this plan adds", and the Review Focus list.
  - Fix the findings, test first.
  - Fast-forward `ecosystem` into `main` (from the worktree, `git -C C:\dev\mifitness-re merge --ff-only ecosystem` only if the main checkout is clean and on `main`; otherwise ask), and ask the user before pushing.

---

### Task 7: Release 0.3.1 (with the user)

There is no new code here. Every outward step waits for the user's go-ahead. A registry version cannot be withdrawn: say so when asking for the tag.

- [ ] **Step 1 (me, after the user agrees): push `main`.** `CI` must be green.

- [ ] **Step 2 (me, optional, after the user agrees): TestPyPI dry run.**
  - Run `gh workflow run release.yml --ref main`, then `gh run watch`.
  - Expected:
    - `test`, `build`, `smoke`, `mcpb` (if Task 5 was done) and `publish-testpypi` are green;
    - `publish-pypi`, `publish-mcp-registry` and `github-release` are skipped.

- [ ] **Step 3 (me): the release date.** Set `## 0.3.1 — <today>` in the CHANGELOG. Commit as `docs: 0.3.1 release date`, and push with the user's go-ahead.

- [ ] **Step 4 (me, after the user agrees): tag and push.**

```bash
git tag -a v0.3.1 -m "mifit 0.3.1"
git push origin v0.3.1
```

  Expected:
  - every job is green except `publish-testpypi`, which is skipped;
  - `https://pypi.org/pypi/mifit/0.3.1/json`: its `info.description` contains `mcp-name: io.github.Jenwein/mifit`;
  - `https://registry.modelcontextprotocol.io/v0/servers?search=io.github.Jenwein/mifit` lists version 0.3.1 as the latest;
  - the GitHub Release `v0.3.1` has the wheel, the sdist and (Task 5) `mifit-0.3.1.mcpb`.

  If `publish-mcp-registry` fails:
  - Read its log. Never re-push or move the tag, and never touch the PyPI release.
  - The cause is outside the repository (PyPI still not listing the version, a registry outage): `gh run rerun <run-id> --failed` reruns only that job, with a fresh OIDC token.
  - The cause is in `server.json`: the fix ships as 0.3.2, because the registry reads the README of the PyPI version it lists, and the tag build checks `server.json` against the tag.

- [ ] **Step 5 (me and the user): check from the release.**
  - Run `python tools/mcp_handshake.py uvx mifit@0.3.1 mcp`. Expected: 16 tools.
  - If Task 5 was done: the user replaces the 0.3.0 test extension with `mifit-0.3.1.mcpb` from the GitHub Release and calls `get_status` once.

- [ ] **Step 6 (me): close out.**
  - Roadmap: E becomes "✅ 0.3.1 已发布（<date>）；MCP Registry `io.github.Jenwein/mifit`；`.mcpb` 附在 GitHub Release" (or "`.mcpb` 推迟（D16）：<原因>").
  - Memory: update `release-and-mcp`: E is done; the name is `io.github.Jenwein/mifit` (the registry is case-sensitive); a registry version cannot be withdrawn; how the bundle is checked.
  - Commit, and push with the user's go-ahead.
