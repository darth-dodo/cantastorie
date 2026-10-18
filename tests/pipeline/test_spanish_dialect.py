"""Spanish stories are written in neutral Latin American Spanish (AI-509).

The writer and the reviser are told the variety in their per-call message, so
the shared instructions, and other languages' prompts and cache keys, stay as
they were.
"""

from pathlib import Path

from src.pipeline.steps.revise import revise_story
from src.pipeline.steps.write import (
    LANGUAGE_NAMES,
    StoryDraft,
    dialect_note,
    story_from_draft,
    write_story,
)
from tests.pipeline.test_authoring_steps import DraftModel, _cache, _settings, good_draft


def test_spanish_is_named_latin_american_spanish() -> None:
    assert LANGUAGE_NAMES["es"] == "Latin American Spanish"


def test_the_dialect_note_rules_out_spain_forms() -> None:
    note = dialect_note("es")
    assert "vosotros" in note
    assert "ustedes" in note
    assert "voseo" in note
    assert dialect_note("it") == ""
    assert dialect_note("en") == ""


def test_the_spanish_writer_is_asked_for_latin_american_spanish(tmp_path: Path) -> None:
    model = DraftModel(good_draft())
    write_story("the_sleepy_sea", "es", _settings(), _cache(tmp_path), model=model)
    assert any("Latin American Spanish" in seen for seen in model.seen_prompts)
    assert any(dialect_note("es") in seen for seen in model.seen_prompts)


def test_other_writers_get_no_dialect_note(tmp_path: Path) -> None:
    model = DraftModel(good_draft())
    write_story("the_sleepy_sea", "it", _settings(), _cache(tmp_path), model=model)
    assert not any("vosotros" in seen for seen in model.seen_prompts)


def test_the_spanish_reviser_keeps_latin_american_spanish(tmp_path: Path) -> None:
    story = story_from_draft(
        StoryDraft(title="t", pages=good_draft().pages),
        story_id="s-es",
        theme="the_sleepy_sea",
        language="es",
    )
    reviser = DraftModel(good_draft())
    revise_story(story, ["page p1 is too short"], _settings(), _cache(tmp_path), model=reviser)
    assert dialect_note("es") in reviser.seen_prompts[0]
