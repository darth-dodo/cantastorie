"""sentry-sdk 3 drops init options src/observability.py passes (PR #218, F4).

The runtime constraint, the lockfile and the pre-commit mypy hook must agree
on a 2.x release.
"""

import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _sentry_requirement() -> Requirement:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return next(Requirement(d) for d in deps if Requirement(d).name == "sentry-sdk")


def _locked_sentry() -> Version:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return Version(next(p["version"] for p in lock["package"] if p["name"] == "sentry-sdk"))


def _hook_sentry() -> Requirement:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "mypy"]
    deps = [Requirement(d) for d in hooks[0]["additional_dependencies"]]
    return next(d for d in deps if d.name == "sentry-sdk")


def test_the_runtime_constraint_refuses_sentry_sdk_3() -> None:
    spec = _sentry_requirement().specifier
    assert spec.contains("2.0.0")
    assert not spec.contains("3.0.0")
    assert not spec.contains("3.0.1")


def test_the_lock_and_the_mypy_hook_agree_on_a_2x_release() -> None:
    locked = _locked_sentry()
    assert _sentry_requirement().specifier.contains(str(locked))
    assert _hook_sentry().specifier.contains(str(locked))
    assert locked.major == 2
