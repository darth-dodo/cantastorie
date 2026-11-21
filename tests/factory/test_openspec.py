from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_openspec_is_configured_for_cantastorie() -> None:
    config = yaml.safe_load((ROOT / "openspec" / "config.yaml").read_text())
    assert config["schema"] == "spec-driven"
    context = config["context"]
    assert "Cantastorie" in context
    assert "openspec-apply-change" in context  # names the denied skill, as Maya's does


def test_openspec_folders_exist() -> None:
    assert (ROOT / "openspec" / "changes").is_dir()
    assert (ROOT / "openspec" / "specs").is_dir()
