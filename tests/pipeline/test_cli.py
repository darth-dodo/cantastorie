"""Behavior specs for the CLI.

generate runs the whole authoring pass and stages a story; publish uploads a
staged story to R2. Both validate the locked vocabularies (product.md **5
languages** and the theme list). The heavy lifting is proven in test_generate
and test_publish; here we prove the CLI wiring and its guardrails, so the
provider-driven functions are stubbed.
"""

from pathlib import Path
from typing import get_args

import pytest
from typer.testing import CliRunner

from src.pipeline import cli
from src.pipeline.cli import app
from src.pipeline.models import Language
from src.pipeline.prompts import PromptLine, PromptPublishResult
from src.pipeline.publish import AuditResult, PublishResult

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

    def fake_generate(
        theme: str,
        language: str,
        settings: object,
        shape: str = "linear",
        premise: str | None = None,
    ) -> Path:
        seen.update(theme=theme, language=language, shape=shape)
        return tmp_path / "staging" / "the-sleepy-sea-it-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "it"])

    assert result.exit_code == 0
    assert seen == {"theme": "the_sleepy_sea", "language": "it", "shape": "linear"}
    assert "the-sleepy-sea-it-abc12345" in result.output


def test_generate_forwards_the_branching_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Given --shape branching,
    When generate is invoked,
    Then the shape reaches the authoring run."""
    seen: dict[str, object] = {}

    def fake_generate(
        theme: str,
        language: str,
        settings: object,
        shape: str = "linear",
        premise: str | None = None,
    ) -> Path:
        seen["shape"] = shape
        return tmp_path / "staging" / "the-sleepy-sea-it-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(
        app,
        ["generate", "--theme", "the_sleepy_sea", "--language", "it", "--shape", "branching"],
    )

    assert result.exit_code == 0
    assert seen["shape"] == "branching"


def test_generate_rejects_a_shape_outside_the_locked_set() -> None:
    """Given the shapes linear/branching,
    When generate is invoked with an unknown shape,
    Then the command fails and the message names the rejected shape.
    """
    result = runner.invoke(
        app,
        ["generate", "--theme", "the_sleepy_sea", "--language", "it", "--shape", "zigzag"],
    )
    assert result.exit_code != 0
    assert "zigzag" in result.output


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


def test_generate_rejects_a_premise_over_the_bound() -> None:
    """AI-470: the same 300-character bound as PackRequest applies here — the
    CLI is another way to reach the writer prompt, not a separate rule."""
    result = runner.invoke(
        app,
        [
            "generate",
            "--theme",
            "the_sleepy_sea",
            "--language",
            "it",
            "--premise",
            "x" * 301,
        ],
    )
    assert result.exit_code != 0
    assert "300" in result.output


def test_generate_accepts_a_premise_at_the_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AI-470: exactly 300 characters is the bound, not the cutoff — guards
    against an off-by-one (`>=` instead of `>`) in the CLI's own check."""
    seen: dict[str, object] = {}

    def fake_generate(
        theme: str,
        language: str,
        settings: object,
        shape: str = "linear",
        premise: str | None = None,
    ) -> Path:
        seen["premise"] = premise
        return tmp_path / "staging" / "the-sleepy-sea-it-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(
        app,
        [
            "generate",
            "--theme",
            "the_sleepy_sea",
            "--language",
            "it",
            "--premise",
            "x" * 300,
        ],
    )

    assert result.exit_code == 0
    assert seen["premise"] == "x" * 300


def test_generate_forwards_an_optional_premise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Given a --premise,
    When generate is invoked,
    Then the premise is forwarded to the authoring run."""
    seen: dict[str, object] = {}

    def fake_generate(
        theme: str,
        language: str,
        settings: object,
        shape: str = "linear",
        premise: str | None = None,
    ) -> Path:
        seen.update(theme=theme, language=language, premise=premise)
        return tmp_path / "staging" / "gentle-forest-friends-en-abc12345"

    monkeypatch.setattr(cli, "generate_story", fake_generate)

    result = runner.invoke(
        app,
        [
            "generate",
            "--theme",
            "gentle_forest_friends",
            "--language",
            "en",
            "--premise",
            "Bruno bear has a surprise party.",
        ],
    )

    assert result.exit_code == 0
    assert seen["premise"] == "Bruno bear has a surprise party."


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


def test_audit_reports_zero_violations_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a clean bucket with no violations,
    When audit is invoked,
    Then it exits 0 and reports the manifest count.
    """

    def fake_audit(settings: object) -> AuditResult:
        return AuditResult(violations=[], manifests_checked=2)

    monkeypatch.setattr(cli, "audit_published_bucket", fake_audit)

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0
    assert "2 manifests checked" in result.output
    assert "0 violations" in result.output


def test_audit_reports_violations_and_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given violations in the bucket,
    When audit is invoked,
    Then it exits 1 and lists each violation.
    """

    def fake_audit(settings: object) -> AuditResult:
        return AuditResult(
            violations=["bad-story: story.json missing", "orphan: unlisted directory"],
            manifests_checked=1,
        )

    monkeypatch.setattr(cli, "audit_published_bucket", fake_audit)

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 1
    assert "2 violations" in result.output
    assert "bad-story" in result.output
    assert "orphan" in result.output


# ---------------------------------------------------------------------------
# publish-prompts (H6, AI-481): the operator's spoken-prompt run
# ---------------------------------------------------------------------------


def _fake_prompt_run(seen: list[tuple[str, bool]]) -> object:
    def fake(
        language: str, settings: object, *, dry_run: bool = False, force: bool = False
    ) -> PromptPublishResult:
        seen.append((language, dry_run))
        line = PromptLine(name="offline", manifest_key="offline", text="t", cached=False, url=None)
        return PromptPublishResult(
            language=language,
            dry_run=dry_run,
            target=f"published/{language}/manifest.json",
            lines=[line],
            uploaded=[] if dry_run else ["k"],
            skipped=[],
            manifest_changed=True,
        )

    return fake


def test_publish_prompts_refuses_the_shared_bucket_without_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given no --yes and no --dry-run,
    When publish-prompts targets R2,
    Then it refuses before any TTS call or write, and says how to proceed."""
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "publish_prompts", _fake_prompt_run(seen))

    result = runner.invoke(app, ["publish-prompts", "--language", "es"])

    assert result.exit_code == 1
    assert seen == []
    assert "--yes" in result.output


def test_publish_prompts_with_yes_publishes_the_language(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "publish_prompts", _fake_prompt_run(seen))

    result = runner.invoke(app, ["publish-prompts", "--language", "es", "--yes"])

    assert result.exit_code == 0
    assert seen == [("es", False)]
    assert "1 uploaded" in result.output


def test_publish_prompts_dry_run_needs_no_yes_and_covers_all_languages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "publish_prompts", _fake_prompt_run(seen))

    result = runner.invoke(app, ["publish-prompts", "--language", "all", "--dry-run"])

    assert result.exit_code == 0
    assert seen == [(lang, True) for lang in get_args(Language)]
    assert "8 TTS call(s)" in result.output


def test_publish_prompts_local_writes_dev_fixtures_without_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--local never touches the bucket, so it needs no --yes."""
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "publish_prompts", _fake_prompt_run([]))
    monkeypatch.setattr(cli, "write_dev_prompts", _fake_prompt_run(seen))

    result = runner.invoke(app, ["publish-prompts", "--language", "de", "--local"])

    assert result.exit_code == 0
    assert seen == [("de", False)]


def test_publish_prompts_rejects_a_language_outside_the_roster() -> None:
    result = runner.invoke(app, ["publish-prompts", "--language", "fr", "--dry-run"])
    assert result.exit_code == 1
    assert "fr" in result.output


def test_publish_prompts_reports_a_complete_language_as_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A language whose live manifest already lists every prompt is reported
    "skipped (complete)", on a dry run too, and costs no TTS."""
    forced: list[bool] = []

    def fake(
        language: str, settings: object, *, dry_run: bool = False, force: bool = False
    ) -> PromptPublishResult:
        forced.append(force)
        line = PromptLine(name="offline", manifest_key="offline", text="t", cached=False, url=None)
        return PromptPublishResult(
            language=language,
            dry_run=dry_run,
            target=f"published/{language}/manifest.json",
            lines=[line],
            uploaded=[],
            skipped=[],
            manifest_changed=False,
            skip_reason="complete",
        )

    monkeypatch.setattr(cli, "publish_prompts", fake)

    result = runner.invoke(app, ["publish-prompts", "--language", "it", "--dry-run"])

    assert result.exit_code == 0
    assert "it: skipped (complete)" in result.output
    assert "Would make 0 TTS call(s)" in result.output
    assert forced == [False]

    runner.invoke(app, ["publish-prompts", "--language", "it", "--yes", "--force"])
    assert forced == [False, True]


def test_publish_prompts_exits_one_when_a_language_has_no_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(
        language: str, settings: object, *, dry_run: bool = False, force: bool = False
    ) -> PromptPublishResult:
        return PromptPublishResult(
            language=language,
            dry_run=dry_run,
            target=f"published/{language}/manifest.json",
            lines=[],
            uploaded=[],
            skipped=[],
            manifest_changed=False,
            skip_reason="no manifest",
        )

    monkeypatch.setattr(cli, "publish_prompts", fake)

    result = runner.invoke(app, ["publish-prompts", "--language", "es", "--yes"])

    assert result.exit_code == 1
    assert "es: refused" in result.output
    assert "--force" in result.output
