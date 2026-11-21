"""The smoke check reports and never blocks a deploy."""

from __future__ import annotations

import os
import subprocess
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


def test_permissions_and_the_main_branch_filter() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert workflow[True]["workflow_run"]["branches"] == ["main"]


def test_workflow_run_only_after_a_green_ci() -> None:
    condition = _workflow()["jobs"]["smoke"]["if"]
    assert "workflow_run.conclusion == 'success'" in condition


CURL_SHIM = """#!/usr/bin/env bash
out=""; url=""; write=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift ;; -w) write="$2"; shift ;; http*) url="$1" ;; esac
  shift
done
path="${url#https://cantastorie.onrender.com}"
echo "$path" >> "$SHIM_LOG"
case "$path" in
  /health/version) code="$VERSION_CODE"; body="$VERSION_BODY" ;;
  /health) code="$HEALTH_CODE"; body="$HEALTH_BODY" ;;
  *) code="$PLAY_CODE"; body="" ;;
esac
if [ -n "$out" ]; then printf '%s' "$body" > "$out"; else [ -n "$write" ] || printf '%s' "$body"; fi
[ -n "$write" ] && printf '%s' "$code"
exit 0
"""


def _run_script(tmp_path: Path, **env: str) -> tuple[str, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("curl", CURL_SHIM), ("gh", "#!/usr/bin/env bash\nexit 0\n")):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "log"
    log.write_text("")
    full = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SHIM_LOG": str(log),
        "DRY_RUN": "1",
        "GH_TOKEN": "token",
        "HEAD_SHA": "abc123",
        "VERSION_CODE": "200",
        "VERSION_BODY": '{"commit":"abc123"}',
        "HEALTH_CODE": "200",
        "HEALTH_BODY": '{"status":"ok"}',
        "PLAY_CODE": "200",
        **env,
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/smoke_check.sh")],
        env=full,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    return result.stdout, log.read_text().split()


def test_a_404_on_version_is_a_deploy_in_progress_not_a_problem(tmp_path: Path) -> None:
    out, _ = _run_script(tmp_path, VERSION_CODE="404", VERSION_BODY="")
    assert "deploy in progress" in out
    assert "::warning::" not in out and "would" not in out
    assert "ok:" in out


def test_a_failing_health_is_a_problem_that_would_open_an_issue(tmp_path: Path) -> None:
    out, _ = _run_script(tmp_path, HEALTH_CODE="500", HEALTH_BODY="boom")
    assert "::warning::" in out
    assert "would open an Issue" in out


def test_a_commit_mismatch_notices_but_still_checks_health_and_play(tmp_path: Path) -> None:
    out, calls = _run_script(tmp_path, VERSION_BODY='{"commit":"old999"}')
    assert "::notice::deploy in progress" in out
    assert "::warning::" not in out
    assert "/health" in calls and "/play" in calls


def test_the_poll_step_can_never_stop_the_check_from_running() -> None:
    steps = _workflow()["jobs"]["smoke"]["steps"]
    poll = next(s for s in steps if s.get("name") == "Wait for Render to roll out")
    assert poll["continue-on-error"] is True
