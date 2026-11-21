"""The smoke check reports and never blocks a deploy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((ROOT / ".github/workflows/smoke.yml").read_text())
    return data


def test_runs_hourly_and_after_ci_never_on_push() -> None:
    triggers = _workflow()[True]  # PyYAML reads the "on" key as True
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    assert "push" not in triggers and "pull_request" not in triggers


def test_the_check_step_can_never_fail_the_job() -> None:
    steps = _workflow()["jobs"]["smoke"]["steps"]
    check = next(s for s in steps if s.get("run") == "scripts/smoke_check.sh")
    assert check["continue-on-error"] is True


def test_the_script_checks_the_three_endpoints() -> None:
    text = (ROOT / "scripts/smoke_check.sh").read_text()
    for path in ("/health", "/play", "/health/version"):
        assert path in text
