"""Ported skills are cantastorie's: valid frontmatter, no Maya leftovers, only real commands."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".claude" / "skills"
FACTORY = [
    "ask",
    "plan",
    "build",
    "test",
    "review",
    "pause",
    "resume",
    "cleanup",
    "retro",
    "improve",
    "release",
    "recap",
    "dashboard",
]
# Cantastorie's own skills, kept as they are: settled-architecture names Supabase as a rejected option.
KEPT = {"settled-architecture", "documentation-conventions"}
FORBIDDEN = re.compile(
    r"\bmix\b|credo|sobelow|Pok[eé]mon|Supabase|\bEcto\b|Phoenix|maya-j56u|maya-factory|darth-dodo/maya|changelog\.d|Herdr|herdr",
    re.I,
)


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def _frontmatter(text: str) -> dict[str, str]:
    block = text.split("---")[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines() if ": " in line)


def test_every_factory_skill_exists_with_its_name() -> None:
    for station in FACTORY:
        meta = _frontmatter(_skill(f"factory-{station}"))
        assert meta["name"] == f"factory:{station}"
        assert meta["description"].startswith("Use ")


def test_no_skill_names_a_maya_command_or_product() -> None:
    for path in SKILLS.glob("*/SKILL.md"):
        if path.parent.name.startswith("openspec-") or path.parent.name in KEPT:
            continue  # openspec-* copied verbatim and product-neutral; KEPT are cantastorie's own
        match = FORBIDDEN.search(path.read_text())
        assert match is None, f"{path.parent.name} still says {match.group(0)!r}"


def test_make_targets_named_in_skills_exist() -> None:
    makefile = (ROOT / "Makefile").read_text()
    targets = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.M))
    for path in SKILLS.glob("*/SKILL.md"):
        for target in re.findall(r"`make ([a-z][a-z-]*)", path.read_text()):
            assert target in targets, (
                f"{path.parent.name} names make {target}, which does not exist"
            )
