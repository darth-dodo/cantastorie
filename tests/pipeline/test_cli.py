"""Behavior specs for the CLI scaffold.

Commands exist, validate the locked vocabularies (product.md **5 languages**
and the theme list in "Content Rules"), and name their delivering issue.
"""

from typer.testing import CliRunner

from src.pipeline.cli import app

runner = CliRunner()


def test_generate_accepts_a_locked_theme_and_language_then_points_at_its_issue() -> None:
    """Given a theme and language from the locked vocabularies,
    When generate is invoked,
    Then the arguments validate and the scaffold exits 2 naming AI-358,
    the issue that delivers generation.
    """
    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "it"])
    assert result.exit_code == 2
    assert "AI-358" in result.output


def test_generate_rejects_a_language_outside_the_locked_set() -> None:
    """Given the locked language set it/es/en/el/de,
    When generate is invoked with "fr",
    Then the command fails and the message names the rejected language.
    """
    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "fr"])
    assert result.exit_code != 0
    assert "fr" in result.output


def test_publish_and_audit_scaffolds_name_their_delivering_issues() -> None:
    """Given the publish and audit scaffolds,
    When each is invoked,
    Then each exits 2 naming its delivering issue (AI-361 and AI-378).
    """
    publish = runner.invoke(app, ["publish", "--story-id", "s1"])
    audit = runner.invoke(app, ["audit"])
    assert publish.exit_code == 2 and "AI-361" in publish.output
    assert audit.exit_code == 2 and "AI-378" in audit.output
