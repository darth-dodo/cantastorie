"""Production deploys are gated on CI and install the locked set (H3, AI-479).

Render's auto-deploy is off (see test_render_previews.py); the only way a
commit reaches production is the CI `deploy` job, which runs after every
other job has passed and POSTs to the Render deploy hook.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = ROOT / "Dockerfile"


def _workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(CI.read_text())
    return workflow


def _deploy_job() -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert "deploy" in jobs, "ci.yml has no deploy job"
    job: dict[str, Any] = jobs["deploy"]
    return job


def test_deploy_waits_for_the_ci_success_gate() -> None:
    needs = _deploy_job()["needs"]
    assert needs in ("ci-success", ["ci-success"])


def test_deploy_runs_only_on_a_push_to_main() -> None:
    condition = _deploy_job()["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "pull_request" not in condition


def test_deploy_fires_the_render_hook_from_a_secret() -> None:
    job = _deploy_job()
    assert "secrets.RENDER_DEPLOY_HOOK_URL" in yaml.safe_dump(job["steps"])
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "curl -fsS -X POST" in run
    # The hook URL is a credential: it reaches the script through env, never
    # interpolated into the script body where a log could echo it.
    assert "secrets." not in run


def test_deploy_skips_cleanly_without_the_secret() -> None:
    run = "\n".join(step.get("run", "") for step in _deploy_job()["steps"])
    assert '-z "$RENDER_DEPLOY_HOOK_URL"' in run
    assert "::notice" in run


def test_ci_success_gate_still_needs_every_check() -> None:
    needs = set(_workflow()["jobs"]["ci-success"]["needs"])
    assert {"lint", "typecheck", "test", "test-js", "e2e", "security", "build", "audit"} <= needs


def test_image_installs_the_locked_dependency_set() -> None:
    dockerfile = DOCKERFILE.read_text()
    assert "uv pip install --system ." not in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile


def test_image_logs_unbuffered() -> None:
    assert "PYTHONUNBUFFERED=1" in DOCKERFILE.read_text()
