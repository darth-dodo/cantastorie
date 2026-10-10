"""Content limits for languages that do not separate words with spaces (AI-508).

Japanese has no spaces between words, so `text.split()` sees a whole page as
one or two "words" and every story would fail the per-page floor. Japanese is
measured in characters instead (spaces and punctuation excluded), with limits
scaled from the word limits. Japanese sentences end on a full stop,
exclamation or question mark with no following space, and Hindi (like
Marathi) may end them on the danda.
"""

from src.pipeline.content_rules import (
    CHARACTER_LIMITS,
    PAGE_COUNT,
    WORD_LIMITS,
    check_story,
    length_note,
    limits_for,
    text_length,
)
from src.pipeline.models import Language, Page, Story

# Ten characters of story text plus 。 — punctuation never counts.
JA_SENTENCE = "うみがしずかにねむる。"  # 10 characters, plus the full stop
# Twelve words, then the danda.
HI_SENTENCE = "छोटी नाव धीरे धीरे पानी पर झूलती है और चाँद मुस्कुराता है।"


def make_story(language: Language, page_texts: list[str]) -> Story:
    pages = [
        Page(
            id=f"p{i}",
            text=text,
            next_page=f"p{i + 1}" if i < len(page_texts) else None,
        )
        for i, text in enumerate(page_texts, start=1)
    ]
    return Story(
        id="story-1",
        language=language,
        title="t",
        theme="the_sleepy_sea",
        shape="linear",
        pages=pages,
    )


def test_japanese_is_measured_in_characters_and_others_in_words() -> None:
    assert limits_for("ja") is CHARACTER_LIMITS
    assert CHARACTER_LIMITS.unit == "characters"
    for language in ("it", "en", "hi", "mr"):
        assert limits_for(language) is WORD_LIMITS
    assert WORD_LIMITS.unit == "words"


def test_character_limits_scale_the_word_limits() -> None:
    assert (CHARACTER_LIMITS.page_min, CHARACTER_LIMITS.page_max) == (75, 175)
    assert (CHARACTER_LIMITS.story_min, CHARACTER_LIMITS.story_max) == (625, 1500)
    assert CHARACTER_LIMITS.sentence_max == 50


def test_japanese_length_excludes_spaces_and_punctuation() -> None:
    assert text_length(JA_SENTENCE, "ja") == 10
    assert text_length("「おやすみ」、 くま。", "ja") == 6


def test_word_languages_still_count_whitespace_words() -> None:
    assert text_length("The water sings shh shh.", "en") == 5
    assert text_length(HI_SENTENCE, "hi") == 12


def test_a_conforming_japanese_story_passes() -> None:
    # 10 sentences of 10 characters = 100 characters per page, 1000 in total.
    page = JA_SENTENCE * 10
    assert check_story(make_story("ja", [page] * PAGE_COUNT)) == []


def test_a_short_japanese_page_fails_in_characters() -> None:
    pages = [JA_SENTENCE * 10] * (PAGE_COUNT - 1) + [JA_SENTENCE * 5]
    violations = check_story(make_story("ja", pages))
    assert [v.rule for v in violations] == ["page_words"]
    assert "50 characters" in violations[0].detail
    assert "75-175" in violations[0].detail


def test_japanese_sentences_split_on_full_stop_without_a_space() -> None:
    # One 60-character sentence breaks the 50-character cap; ten short ones do not.
    long_sentence = "あ" * 60 + "。"
    pages = [JA_SENTENCE * 10] * (PAGE_COUNT - 1) + [long_sentence + JA_SENTENCE * 4]
    violations = check_story(make_story("ja", pages))
    assert [v.rule for v in violations] == ["sentence_cap"]
    assert "60 characters" in violations[0].detail


def test_a_closing_quote_stays_with_its_sentence() -> None:
    quoted = "「おやすみなさい、くまさん。」"  # 11 characters
    page = quoted + JA_SENTENCE * 9  # 11 + 90 = 101
    assert check_story(make_story("ja", [page] + [JA_SENTENCE * 10] * 9)) == []


def test_hindi_sentences_split_on_the_danda() -> None:
    # Three 12-word sentences: within the 20-word cap only if the danda splits.
    page = " ".join([HI_SENTENCE] * 3)  # 36 words
    assert check_story(make_story("hi", [page] * PAGE_COUNT)) == []


def test_length_note_is_only_given_for_character_languages() -> None:
    assert length_note("en") == ""
    assert length_note("hi") == ""
    note = length_note("ja")
    assert "characters" in note
    assert "75-175" in note
    assert "625-1500" in note
    assert "50" in note
