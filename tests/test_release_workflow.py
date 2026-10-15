"""The release workflow's shape: triggers, permissions and what each job may do.

The workflow publishes to PyPI with a short-lived OIDC token (Trusted
Publishing), so these tests pin who can mint that token, that the publish jobs
run no project code, and that every checkout drops its credentials.
"""

from __future__ import annotations

from pathlib import Path

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
        "github-release",
    }


TAG_PUSH = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def test_only_the_publish_jobs_can_mint_an_oidc_token():
    minted = {n for n, j in JOBS.items() if j.get("permissions", {}).get("id-token") == "write"}
    assert minted == {"publish-pypi", "publish-testpypi"}
    assert JOBS["publish-pypi"]["permissions"] == {"id-token": "write"}
    assert JOBS["publish-testpypi"]["permissions"] == {"id-token": "write"}


def test_only_the_github_release_job_can_write_to_the_repository():
    writers = {n for n, j in JOBS.items() if j.get("permissions", {}).get("contents") == "write"}
    assert writers == {"github-release"}


def test_publish_jobs_run_no_project_code():
    for name in ("publish-pypi", "publish-testpypi"):
        assert not [u for u in _uses(JOBS[name]) if u.startswith("actions/checkout")], name
        assert "pypa/gh-action-pypi-publish@release/v1" in _uses(JOBS[name]), name
        assert set(JOBS[name]["needs"]) == {"test", "build", "smoke"}, name


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


def test_github_release_follows_the_pypi_publish():
    job = JOBS["github-release"]
    assert job["permissions"] == {"contents": "write"}
    assert "publish-pypi" in job["needs"] and job["if"] == TAG_PUSH
    assert "tools/release.py notes" in _runs(job) and "gh release create" in _runs(job)


def test_every_checkout_drops_its_credentials():
    for name, job in JOBS.items():
        for step in _steps(job):
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False, name
