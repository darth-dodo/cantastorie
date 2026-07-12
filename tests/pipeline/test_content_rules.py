"""Behavior specs for content limits as code.

docs/product.md "Content Rules" — **Linear stories**: 10 pages, 30-70 words
per page, 250-600 total, a 20-word sentence cap; and **Branching stories**:
choice labels count as story text for every limit. These are enforced by
pure functions, not prompt hope (AI-358).
"""

from src.pipeline.content_rules import (
    PAGE_COUNT,
    PAGE_WORDS_MAX,
    PAGE_WORDS_MIN,
    SENTENCE_WORDS_MAX,
    STORY_WORDS_MIN,
    check_story,
)
from src.pipeline.models import ChoiceOption, ChoicePoint, Page, Story

# Five words per sentence; repeated it composes pages of any 5-multiple size.
SENTENCE = "The water sings shh shh."
TWENTY_ONE_WORD_SENTENCE = (
    "The little boat rocks and rocks and rocks and rocks and rocks and rocks "
    "and rocks and rocks and rocks and rocks tonight."
)


def page_text(sentences: int) -> str:
    return " ".join([SENTENCE] * sentences)


def make_story(page_texts: list[str]) -> Story:
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
        language="it",
        title="La barchetta",
        theme="the_sleepy_sea",
        shape="linear",
        pages=pages,
    )


def conforming_story() -> Story:
    # 10 pages x 40 words = 400 total: inside every limit.
    return make_story([page_text(8)] * PAGE_COUNT)


def test_a_conforming_ten_page_story_passes_every_content_limit() -> None:
    """Given a 10-page story with 40-word pages of 5-word sentences,
    When content rules are checked,
    Then no violations are reported.
    """
    assert check_story(conforming_story()) == []


def test_a_story_without_exactly_ten_pages_fails_validation() -> None:
    """Given a story of 7 pages (product.md **Linear stories**: 10 pages),
    When content rules are checked,
    Then a page-count violation is reported.
    """
    violations = check_story(make_story([page_text(8)] * 7))
    assert [v.rule for v in violations] == ["page_count"]
    assert str(PAGE_COUNT) in violations[0].detail


def test_a_page_under_thirty_words_fails_validation() -> None:
    """Given one page of 25 words (below the 30-word floor),
    When content rules are checked,
    Then a page-words violation names that page.
    """
    texts = [page_text(8)] * (PAGE_COUNT - 1) + [page_text(5)]
    violations = check_story(make_story(texts))
    assert [(v.rule, v.page_id) for v in violations] == [("page_words", f"p{PAGE_COUNT}")]
    assert str(PAGE_WORDS_MIN) in violations[0].detail


def test_a_page_over_seventy_words_fails_validation() -> None:
    """Given one page of 75 words (above the 70-word cap),
    When content rules are checked,
    Then a page-words violation names that page.
    """
    texts = [page_text(15)] + [page_text(8)] * (PAGE_COUNT - 1)
    violations = check_story(make_story(texts))
    assert [(v.rule, v.page_id) for v in violations] == [("page_words", "p1")]
    assert str(PAGE_WORDS_MAX) in violations[0].detail


def test_a_story_under_250_total_words_fails_validation() -> None:
    """Given 5 pages of 20 words (100 total, below the 250 floor),
    When content rules are checked,
    Then a story-words violation is reported alongside the page-count and
    page-words violations (20 words is below the 30-word floor too).
    """
    violations = check_story(make_story([page_text(4)] * 5))
    rules = {v.rule for v in violations}
    assert "story_words" in rules
    assert str(STORY_WORDS_MIN) in next(v.detail for v in violations if v.rule == "story_words")


def test_a_sentence_longer_than_twenty_words_fails_validation() -> None:
    """Given a page containing a 21-word sentence (over the 20-word cap),
    When content rules are checked,
    Then a sentence-cap violation names that page.
    """
    long_page = f"{TWENTY_ONE_WORD_SENTENCE} {page_text(3)}"  # 36 words: page limits ok
    texts = [page_text(8)] * (PAGE_COUNT - 1) + [long_page]
    violations = check_story(make_story(texts))
    assert [(v.rule, v.page_id) for v in violations] == [("sentence_cap", f"p{PAGE_COUNT}")]
    assert str(SENTENCE_WORDS_MAX) in violations[0].detail


def test_choice_labels_count_as_story_text_for_every_limit() -> None:
    """Given a page whose choice label is 21 words (product.md: choice labels
    count as story text for every limit),
    When content rules are checked,
    Then the label trips the sentence cap.
    """
    story = conforming_story()
    story.pages[3].next_page = None
    story.pages[3].choice = ChoicePoint(
        options=(
            ChoiceOption(label=TWENTY_ONE_WORD_SENTENCE, next_page="p5"),
            ChoiceOption(label="Sail to the moon path", next_page="p5"),
        )
    )
    violations = check_story(story)
    rules = {(v.rule, v.page_id) for v in violations}
    # 40 text words + 21 + 5 label words = 66: within page limits, so only
    # the sentence cap trips here — but the words were counted, see below.
    assert ("sentence_cap", "p4") in rules

    # Push the same page over 70 words purely with label text.
    story.pages[3].text = page_text(12)  # 60 words
    violations = check_story(story)
    assert ("page_words", "p4") in {(v.rule, v.page_id) for v in violations}
