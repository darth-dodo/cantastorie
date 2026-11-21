"""github_setup.sh only uses idempotent gh calls and defines every label the factory uses."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "factory" / "github_setup.sh"
LABELS = {
    "bug",
    "feature",
    "paused",
    "needs-info",
    "ready-for-human",
    "epic",
    "status:in-progress",
    "priority:urgent",
    "priority:high",
    "priority:medium",
    "priority:low",
}


def test_every_label_is_created_with_force() -> None:
    text = SCRIPT.read_text()
    assert 'gh label create "$1"' in text and "--force" in text
    created = set(re.findall(r'^label "([^"]+)"', text, flags=re.MULTILINE))
    assert created == LABELS


def test_the_script_stops_on_error_and_asks_before_protecting_main() -> None:
    text = SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    assert "read -r -p" in text  # asks before branch protection


def test_board_fields_are_checked_before_they_are_created() -> None:
    text = SCRIPT.read_text()
    assert "Status" in text and "Estimate" in text
    assert text.index("field-list") < text.index("field-create")
    assert "--jq" in text and '"title":"Cantastorie"' not in text
