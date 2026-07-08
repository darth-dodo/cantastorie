"""Behavior specs for the CLI.

generate runs the whole authoring pass and stages a story; publish uploads a
staged story to R2. Both validate the locked vocabularies (product.md **5
languages** and the theme list). The heavy lifting is proven in test_generate
and test_publish; here we prove the CLI wiring and its guardrails, so the
provider-driven functions are stubbed.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.pipeline import cli
from src.pipeline.cli import app
from src.pipeline.publish import PublishResult

runner = CliRunner()


def test_generate_validates_then_runs_the_pass_and_reports_the_staging_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Given a locked theme and language,
    When generate is invoked,
    Then it validates the vocabularies, runs the authoring pass, and reports
    where the story was staged for review.
    """
    seen: dict[str, object] = {}

    def fake_generate(theme: str, language: str, settings: object) -> Path:
        seen.update(theme=theme, language=language)
        return tmp_path / "staging" / "the-sleepy-sea-it-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "it"])

    assert result.exit_code == 0
    assert seen == {"theme": "the_sleepy_sea", "language": "it"}
    assert "the-sleepy-sea-it-abc12345" in result.output


def test_generate_rejects_a_language_outside_the_locked_set() -> None:
    """Given the locked language set it/es/en/el/de,
    When generate is invoked with "fr",
    Then the command fails and the message names the rejected language.
    """
    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "fr"])
    assert result.exit_code != 0
    assert "fr" in result.output


def test_generate_rejects_a_theme_outside_the_locked_set() -> None:
    """Given the locked theme list,
    When generate is invoked with an unknown theme,
    Then the command fails and the message names the rejected theme.
    """
    result = runner.invoke(app, ["generate", "--theme", "dragons", "--language", "it"])
    assert result.exit_code != 0
    assert "dragons" in result.output


def test_publish_uploads_a_staged_story_and_reports_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a staged story id,
    When publish is invoked,
    Then it publishes the story and reports how much it uploaded, skipped, and
    how many stories the manifest now lists.
    """
    seen: dict[str, object] = {}

    def fake_publish(story_id: str, settings: object) -> PublishResult:
        seen["story_id"] = story_id
        return PublishResult(
            story_id=story_id,
            uploaded=["a", "b"],
            skipped=["c"],
            manifest_story_ids=[story_id],
        )

    monkeypatch.setattr(cli, "publish_story", fake_publish)

    result = runner.invoke(app, ["publish", "--story-id", "the-sleepy-sea-it-abc12345"])

    assert result.exit_code == 0
    assert seen["story_id"] == "the-sleepy-sea-it-abc12345"
    assert "2 uploaded" in result.output
    assert "1 unchanged" in result.output


def test_audit_scaffold_still_names_its_delivering_issue() -> None:
    """Given the audit scaffold,
    When it is invoked,
    Then it exits 2 naming AI-378, the issue that delivers it.
    """
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 2 and "AI-378" in result.output
