"""Content limits as code, not prompt hope.

docs/product.md "Content Rules" (**Linear stories**): 8 pages, 30-70 words
per page, 250-600 total, a 12-word sentence cap. Choice labels count as
story text for every limit (**Branching stories**). The writer's prompt
carries the same rules, but only these pure functions decide.
"""

import re
from typing import Literal

from pydantic import BaseModel

from src.pipeline.models import Page, Story

PAGE_COUNT = 8
PAGE_WORDS_MIN = 30
PAGE_WORDS_MAX = 70
STORY_WORDS_MIN = 250
STORY_WORDS_MAX = 600
SENTENCE_WORDS_MAX = 12

ContentRule = Literal["page_count", "page_words", "story_words", "sentence_cap"]

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


class ContentViolation(BaseModel):
    """One broken content limit, precise enough to drive a targeted revise."""

    rule: ContentRule
    page_id: str | None = None
    detail: str


def words(text: str) -> list[str]:
    return text.split()


def sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BOUNDARY.split(text.strip()) if part]


def page_word_count(page: Page) -> int:
    """Words on a page; choice labels count as story text for every limit."""
    count = len(words(page.text))
    if page.choice is not None:
        count += sum(len(words(option.label)) for option in page.choice.options)
    return count


def _page_sentences(page: Page) -> list[str]:
    """Sentences on a page; each choice label is judged as its own sentence."""
    result = sentences(page.text)
    if page.choice is not None:
        result.extend(option.label for option in page.choice.options)
    return result


def check_story(story: Story) -> list[ContentViolation]:
    """Every content-limit violation in the story, or [] when it conforms."""
    violations: list[ContentViolation] = []

    if len(story.pages) != PAGE_COUNT:
        violations.append(
            ContentViolation(
                rule="page_count",
                detail=f"story has {len(story.pages)} pages; exactly {PAGE_COUNT} required",
            )
        )

    total_words = 0
    for page in story.pages:
        count = page_word_count(page)
        total_words += count
        if not PAGE_WORDS_MIN <= count <= PAGE_WORDS_MAX:
            violations.append(
                ContentViolation(
                    rule="page_words",
                    page_id=page.id,
                    detail=(
                        f"page {page.id} has {count} words; "
                        f"{PAGE_WORDS_MIN}-{PAGE_WORDS_MAX} required"
                    ),
                )
            )
        for sentence in _page_sentences(page):
            length = len(words(sentence))
            if length > SENTENCE_WORDS_MAX:
                violations.append(
                    ContentViolation(
                        rule="sentence_cap",
                        page_id=page.id,
                        detail=(
                            f"page {page.id} sentence has {length} words, over the "
                            f"{SENTENCE_WORDS_MAX}-word cap: {sentence!r}"
                        ),
                    )
                )

    if not STORY_WORDS_MIN <= total_words <= STORY_WORDS_MAX:
        violations.append(
            ContentViolation(
                rule="story_words",
                detail=(
                    f"story has {total_words} words; {STORY_WORDS_MIN}-{STORY_WORDS_MAX} required"
                ),
            )
        )

    return violations
