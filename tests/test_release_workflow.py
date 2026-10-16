"""The release workflow's shape: triggers, permissions and what each job may do.

The workflow publishes to PyPI with a short-lived OIDC token (Trusted
Publishing), so these tests pin who can mint that token, that the publish jobs
run no project code, and that every checkout drops its credentials.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
JOBS = WF["jobs"]


def _on() -> dict:
    # PyYAML follows YAML 1.1, where the bare key `on` reads as True.
    return WF.get("on", WF.get(True))


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _uses(job: dict) -> list[str]:
    return [step["uses"] for step in _steps(job) if "uses" in step]


def _runs(job: dict) -> str:
    return "\n".join(step["run"] for step in _steps(job) if "run" in step)


def _env_name(job: dict) -> str:
    env = job["environment"]
    return env["name"] if isinstance(env, dict) else env


def test_triggers_are_version_tags_and_manual_runs_only():
    on = _on()
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"] == {"tags": ["v*"]}


def test_default_permissions_are_read_only():
    assert WF["permissions"] == {"contents": "read"}


def test_jobs():
    assert set(JOBS) == {
        "test",
        "build",
        "smoke",
        "publish-pypi",
        "publish-testpypi",
        "mcpb",
        "publish-mcp-registry",
        "github-release",
    }


TAG_PUSH = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def test_only_the_publish_jobs_can_mint_an_oidc_token():
    minted = {n for n, j in JOBS.items() if j.get("permissions", {}).get("id-token") == "write"}
    assert minted == {"publish-pypi", "publish-testpypi", "publish-mcp-registry"}
    assert JOBS["publish-pypi"]["permissions"] == {"id-token": "write"}
    assert JOBS["publish-testpypi"]["permissions"] == {"id-token": "write"}
    assert JOBS["publish-mcp-registry"]["permissions"] == {"id-token": "write"}


def test_only_the_github_release_job_can_write_to_the_repository():
    writers = {n for n, j in JOBS.items() if j.get("permissions", {}).get("contents") == "write"}
    assert writers == {"github-release"}


def test_publish_jobs_run_no_project_code():
    for name in ("publish-pypi", "publish-testpypi"):
        assert not [u for u in _uses(JOBS[name]) if u.startswith("actions/checkout")], name
        assert "pypa/gh-action-pypi-publish@release/v1" in _uses(JOBS[name]), name
        assert set(JOBS[name]["needs"]) == {"test", "build", "smoke", "mcpb"}, name


def test_pypi_job_runs_on_version_tags_in_the_pypi_environment():
    job = JOBS["publish-pypi"]
    assert _env_name(job) == "pypi"
    assert job["if"] == TAG_PUSH


def test_testpypi_job_targets_testpypi_and_skips_existing():
    job = JOBS["publish-testpypi"]
    assert _env_name(job) == "testpypi"
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    [publish] = [s for s in _steps(job) if s.get("uses", "").startswith("pypa/")]
    assert publish["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert publish["with"]["skip-existing"] is True


def test_build_job_checks_version_and_dist():
    runs = _runs(JOBS["build"])
    assert "tools/release.py check-version --tag" in runs
    assert "twine check --strict" in runs and "tools/release.py check-dist dist" in runs
    [check] = [s for s in _steps(JOBS["build"]) if "check-version" in s.get("run", "")]
    assert check["if"] == "github.ref_type == 'tag'"
    assert '--tag "$GITHUB_REF_NAME"' in check["run"]
    # The registry grants io.github.<owner>/* only to that owner's workflows.
    assert '--owner "$GITHUB_REPOSITORY_OWNER"' in check["run"]
    assert "server.json" in check["name"] and "bundle" in check["name"]


def test_build_job_uploads_server_json_before_running_anything():
    # What the registry job publishes is the checked-out file, before any build
    # tool or project code has run in the workspace.
    steps = _steps(JOBS["build"])
    [upload] = [i for i, s in enumerate(steps) if s.get("with", {}).get("name") == "server-json"]
    first_run = min(i for i, s in enumerate(steps) if "run" in s)
    assert steps[0]["uses"].startswith("actions/checkout") and upload < first_run


def test_smoke_installs_the_built_wheel_on_three_systems_without_checkout():
    job = JOBS["smoke"]
    assert set(job["strategy"]["matrix"]["os"]) == {
        "ubuntu-latest",
        "windows-latest",
        "macos-latest",
    }
    assert not [u for u in _uses(job) if u.startswith("actions/checkout")]
    assert "mifit --version" in _runs(job)


def test_smoke_runs_the_mcp_server_over_stdio():
    runs = _runs(JOBS["smoke"])
    assert '["mifit", "mcp"]' in runs and "tools/list" in runs and "get_status" in runs
    # The server writes UTF-8; the Windows runner's locale code page is not.
    assert 'encoding="utf-8"' in runs
    # Like a client, it waits for each reply before closing stdin: the server stops
    # at the end of stdin and drops requests still in flight.
    assert "subprocess.Popen(" in runs and "stdout.readline()" in runs


def test_github_release_follows_the_pypi_publish():
    job = JOBS["github-release"]
    assert job["permissions"] == {"contents": "write"}
    assert "publish-pypi" in job["needs"] and job["if"] == TAG_PUSH
    assert "tools/release.py notes" in _runs(job) and "gh release create" in _runs(job)
    assert "mcpb" in job["needs"] and "bundle/*.mcpb" in _runs(job)


def test_mcpb_job_packs_checks_and_runs_the_bundle_with_pinned_tools():
    job = JOBS["mcpb"]
    assert job["needs"] in ("build", ["build"]) and "if" not in job and "permissions" not in job
    runs = _runs(job)
    assert "npx --yes @anthropic-ai/mcpb@2.1.2 pack packaging/mcpb" in runs
    assert "npx --yes @anthropic-ai/mcpb@2.1.2 validate packaging/mcpb/manifest.json" in runs
    assert "tools/release.py check-mcpb" in runs
    assert 'tools/mcp_handshake.py --expect-version "$VERSION" uv run' in runs
    assert '--find-links "$PWD/dist"' in runs
    assert "actions/setup-node@v7" in _uses(job)
    uploads = [
        s["with"]["name"]
        for s in _steps(job)
        if s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert uploads == ["mcpb"]


def test_a_broken_bundle_stops_every_publish():
    for name in ("publish-pypi", "publish-testpypi"):
        assert "mcpb" in JOBS[name]["needs"], name


MCP_PUBLISHER = (
    "https://github.com/modelcontextprotocol/registry/releases/download/"
    "v1.8.1/mcp-publisher_linux_amd64.tar.gz"
)
MCP_PUBLISHER_SHA256 = "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc"


def test_build_job_uploads_the_dists_and_server_json():
    uploads = {
        s["with"]["name"]: s["with"]["path"]
        for s in _steps(JOBS["build"])
        if s.get("uses", "").startswith("actions/upload-artifact")
    }
    assert uploads == {"dist": "dist/", "server-json": "server.json"}


def test_registry_job_follows_the_pypi_publish_on_tags():
    job = JOBS["publish-mcp-registry"]
    assert job["if"] == TAG_PUSH and set(job["needs"]) == {"build", "publish-pypi"}


def test_registry_job_runs_a_pinned_checked_publisher_on_the_built_server_json():
    job = JOBS["publish-mcp-registry"]
    assert not [u for u in _uses(job) if u.startswith("actions/checkout")]
    downloads = [
        s for s in _steps(job) if s.get("uses", "").startswith("actions/download-artifact")
    ]
    assert [d["with"]["name"] for d in downloads] == ["server-json"]
    runs = _runs(job)
    assert MCP_PUBLISHER in runs and MCP_PUBLISHER_SHA256 in runs and "sha256sum --check" in runs
    assert "releases/latest" not in runs
    assert "mcp-publisher login github-oidc" in runs
    assert "mcp-publisher publish registry/server.json" in runs
    # validate asks the registry over the network; here, after PyPI, a hiccup in it
    # would fail the job outside the retry loop.
    assert "validate" not in runs


def test_server_json_is_validated_before_anything_is_published():
    # The registry's own check runs in the build job, which every publish needs,
    # so a server.json the registry would refuse stops the release before PyPI.
    [step] = [s for s in _steps(JOBS["build"]) if "mcp-publisher validate" in s.get("run", "")]
    assert "if" not in step  # also on the TestPyPI dry run
    run = step["run"]
    assert MCP_PUBLISHER in run and MCP_PUBLISHER_SHA256 in run and "sha256sum --check" in run
    assert "$RUNNER_TEMP/mcp-publisher validate server.json" in run  # outside the build tree
    names = [s.get("name", s.get("uses", "")) for s in _steps(JOBS["build"])]
    assert names.index(step["name"]) < names.index("Build")  # before any project build code
    for name in ("publish-pypi", "publish-testpypi", "publish-mcp-registry"):
        assert "build" in JOBS[name]["needs"], name


def test_registry_publish_retries_while_pypi_catches_up():
    [step] = [
        s for s in _steps(JOBS["publish-mcp-registry"]) if "publish registry/" in s.get("run", "")
    ]
    run = step["run"]
    assert "for attempt in" in run and "sleep 20" in run
    assert run.index("for attempt in") < run.index("login github-oidc")  # a fresh token per try


BASH = shutil.which("bash")

# Stands in for mcp-publisher: logs each call; publish succeeds from call $SUCCEED_ON on.
FAKE_PUBLISHER = """#!/bin/sh
echo "$*" >> calls.log
if [ "$1" = publish ]; then
  [ "$(grep -c '^publish' calls.log)" -ge "$SUCCEED_ON" ] && exit 0
  exit 1
fi
exit 0
"""


def _bash_works() -> bool:
    # On Windows, bash.exe may be the WSL launcher, with no Linux inside.
    if not BASH:
        return False
    done = subprocess.run([BASH, "-c", "echo ok"], capture_output=True, text=True)
    return done.stdout == "ok\n"


@pytest.mark.skipif(not _bash_works(), reason="needs a working bash")
@pytest.mark.parametrize(("succeed_on", "code", "sleeps"), [(3, 0, 2), (6, 0, 5), (99, 1, 5)])
def test_registry_retry_loop_sleeps_only_between_tries(tmp_path, succeed_on, code, sleeps):
    [step] = [
        s for s in _steps(JOBS["publish-mcp-registry"]) if "publish registry/" in s.get("run", "")
    ]
    publisher = tmp_path / "mcp-publisher"
    publisher.write_bytes(FAKE_PUBLISHER.encode("utf-8"))
    publisher.chmod(0o755)
    script = 'sleep() { echo "sleep $1" >> calls.log; }\n' + step["run"]
    # As the runner runs a `shell: bash` step.
    done = subprocess.run(
        [BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "SUCCEED_ON": str(succeed_on)},
        capture_output=True,
        text=True,
    )
    assert done.returncode == code, done.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("sleep") for line in calls) == sleeps, calls
    assert sum(line.startswith("publish") for line in calls) == min(succeed_on, 6), calls


def test_every_checkout_drops_its_credentials():
    for name, job in JOBS.items():
        for step in _steps(job):
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False, name
