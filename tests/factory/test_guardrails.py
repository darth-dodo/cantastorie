"""The factory's guardrails live in .claude/settings.json (FACTORY.md: Guardrails)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"

REQUIRED_DENY = {
    "Bash(gh pr merge *)",
    "Bash(git push --force *)",
    "Bash(git push -f *)",
    "Bash(git push origin main *)",
    "Read(./.env)",
    "Skill(skill:mattpocock-skills:tdd)",
    "Skill(skill:mattpocock-skills:diagnosing-bugs)",
    "Skill(skill:mattpocock-skills:to-spec)",
    "Skill(skill:mattpocock-skills:to-tickets)",
    "Skill(skill:openspec-apply-change)",
}


def _permissions() -> dict[str, list[str]]:
    return json.loads(SETTINGS.read_text())["permissions"]


def test_every_guardrail_is_denied() -> None:
    assert set(_permissions()["deny"]) >= REQUIRED_DENY


def test_nothing_denied_is_also_allowed() -> None:
    perms = _permissions()
    assert not set(perms["deny"]) & set(perms["allow"])


def test_the_local_gates_are_allowed() -> None:
    allow = set(_permissions()["allow"])
    for gate in ("Bash(make check *)", "Bash(uv run pytest *)", "Bash(npx vitest run *)"):
        assert gate in allow
