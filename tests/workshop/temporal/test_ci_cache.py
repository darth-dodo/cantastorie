"""CI caches the Temporal test server the workflow tests download (task 1.4)."""

from pathlib import Path
from typing import Any

import yaml

CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


def _python_test_steps() -> list[dict[str, Any]]:
    workflow: dict[str, Any] = yaml.safe_load(CI.read_text())
    steps: list[dict[str, Any]] = workflow["jobs"]["test"]["steps"]
    return steps


def test_python_test_job_restores_the_test_server_before_pytest() -> None:
    steps = _python_test_steps()
    names = [step.get("name", "") for step in steps]
    cache = names.index("Cache the Temporal test server")
    pytest_step = next(i for i, step in enumerate(steps) if "pytest" in step.get("run", ""))
    assert cache < pytest_step
    cached = steps[cache]
    assert cached["uses"].startswith("actions/cache@")
    assert cached["with"]["path"] == "~/.cache/temporal-test-server"
    assert "hashFiles('uv.lock')" in cached["with"]["key"]


def test_pytest_downloads_the_test_server_into_the_cached_folder() -> None:
    run = next(step["run"] for step in _python_test_steps() if "pytest" in step.get("run", ""))
    assert 'TEMPORAL_TEST_SERVER_DIR="$HOME/.cache/temporal-test-server"' in run
