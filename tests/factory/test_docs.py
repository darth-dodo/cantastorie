"""The factory's docs exist and name cantastorie's own gates, not Maya's."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (
    "mix ",
    "credo",
    "Pokémon",
    "Pokemon",
    "Supabase",
    "maya-j56u",
    "~/maya-factory",
    "changelog.d",
)


def _text(path: str) -> str:
    return (ROOT / path).read_text()


def test_factory_doc_lists_the_nine_lines() -> None:
    text = _text("FACTORY.md")
    for line in (
        "Intake",
        "Plan",
        "Build",
        "Verify",
        "Review",
        "Check",
        "Gate",
        "Ship",
        "Feedback",
    ):
        assert line in text


def test_docs_name_no_maya_specifics() -> None:
    for path in ("FACTORY.md", "AGENTS.md", "CLAUDE.md", ".github/pull_request_template.md"):
        text = _text(path)
        for word in FORBIDDEN:
            assert word not in text, f"{path} still says {word!r}"


def test_definition_of_done_names_the_local_gates() -> None:
    text = _text("AGENTS.md")
    assert "## Definition of done" in text
    assert "make check" in text and "uv run pytest" in text and "npx vitest run" in text


def test_pr_template_closes_an_issue_and_has_a_verification_list() -> None:
    text = _text(".github/pull_request_template.md")
    assert text.startswith("Closes #")
    assert "## How verified" in text
