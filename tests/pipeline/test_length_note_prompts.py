"""Japanese prompts state the length limits in characters (AI-508).

The shared instructions give limits in words. A Japanese writer, judge or
reviser also receives the same limits restated in characters, through the
per-call message, so the cached instructions for word languages never change.
"""

from pathlib import Path

from src.pipeline.content_rules import length_note
from src.pipeline.models import Story
from src.pipeline.steps.revise import revise_story
from src.pipeline.steps.safety import safety_gate
from src.pipeline.steps.write import StoryDraft, _write_inputs, story_from_draft, write_story
from tests.pipeline.test_authoring_steps import (
    DraftModel,
    JudgeModel,
    _cache,
    _settings,
    report_args,
)

JA_PAGE = "うみがしずかにねむる。" * 10


def ja_draft() -> StoryDraft:
    return StoryDraft(title="うみのよる", pages=[JA_PAGE] * 10)


def ja_story() -> Story:
    return story_from_draft(ja_draft(), story_id="s-ja", theme="the_sleepy_sea", language="ja")


def test_the_japanese_writer_is_told_to_count_characters(tmp_path: Path) -> None:
    model = DraftModel(ja_draft())
    write_story("the_sleepy_sea", "ja", _settings(), _cache(tmp_path), model=model)
    assert any(length_note("ja") in seen for seen in model.seen_prompts)
    assert any("Japanese" in seen for seen in model.seen_prompts)


def test_a_word_language_writer_gets_no_character_note(tmp_path: Path) -> None:
    model = DraftModel(StoryDraft(title="t", pages=["The water sings shh shh. " * 8] * 10))
    write_story("the_sleepy_sea", "hi", _settings(), _cache(tmp_path), model=model)
    assert not any("measured in characters" in seen for seen in model.seen_prompts)
    assert any("Hindi" in seen for seen in model.seen_prompts)


def test_the_japanese_safety_judge_is_told_to_count_characters(tmp_path: Path) -> None:
    judge = JudgeModel(report_args())
    safety_gate(ja_story(), _settings(), _cache(tmp_path), model=judge)
    assert length_note("ja") in judge.seen_prompts[0]


def test_the_japanese_reviser_is_told_to_count_characters(tmp_path: Path) -> None:
    reviser = DraftModel(ja_draft())
    revise_story(ja_story(), ["page p1 is too short"], _settings(), _cache(tmp_path), model=reviser)
    assert length_note("ja") in reviser.seen_prompts[0]


def test_the_marathi_writer_is_told_the_scaled_word_limits(tmp_path: Path) -> None:
    """Marathi words carry attached endings, so its limits are scaled (GitHub #195)."""
    model = DraftModel(StoryDraft(title="t", pages=["चंद्र हसतो आणि तारे चमकतात. " * 6] * 10))
    write_story("the_sleepy_sea", "mr", _settings(), _cache(tmp_path), model=model)
    assert any(length_note("mr") in seen for seen in model.seen_prompts)
    assert any("22-52 words per page" in seen for seen in model.seen_prompts)


def test_the_length_note_is_a_write_cache_input_only_where_it_applies() -> None:
    """A Marathi or Japanese write made under the old prompt is never reused
    (GitHub #195); other languages keep their cache keys."""
    assert _write_inputs("the_sleepy_sea", "mr", _settings())["length_note"] == length_note("mr")
    assert _write_inputs("the_sleepy_sea", "ja", _settings())["length_note"] == length_note("ja")
    for language in ("it", "en", "hi"):
        assert "length_note" not in _write_inputs("the_sleepy_sea", language, _settings())


def test_the_marathi_note_gives_a_target_inside_the_range() -> None:
    note = length_note("mr")
    assert "aim for about 35 words per page" in note
