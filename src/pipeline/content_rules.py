"""Content limits as code, not prompt hope.

docs/product.md "Content Rules" (**Linear stories**): 10 pages, 30-70 words
per page, 250-600 total, a 20-word sentence cap. Choice labels count as
story text for every limit (**Branching stories**). The writer's prompt
carries the same rules, but only these pure functions decide.
"""

import re
from typing import Literal

from pydantic import BaseModel

from src.pipeline.models import Page, Story

PAGE_COUNT = 10
PAGE_WORDS_MIN = 30
PAGE_WORDS_MAX = 70
STORY_WORDS_MIN = 250
STORY_WORDS_MAX = 600
SENTENCE_WORDS_MAX = 20
ARM_PAGES = 4  # pages per branch arm; shared prefix = PAGE_COUNT - ARM_PAGES

ContentRule = Literal[
    "page_count",
    "page_words",
    "story_words",
    "sentence_cap",
    "branch_structure",
    "path_length",
]

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


def heard_paths(story: Story) -> list[list[Page]]:
    """Every path a child can hear: follow next_page, forking at each choice."""
    by_id = {page.id: page for page in story.pages}
    referenced: set[str] = set()
    for page in story.pages:
        if page.next_page:
            referenced.add(page.next_page)
        if page.choice:
            for option in page.choice.options:
                referenced.add(option.next_page)
    entry = next((p for p in story.pages if p.id not in referenced), story.pages[0])

    paths: list[list[Page]] = []

    def walk(page: Page | None, trail: list[Page]) -> None:
        if page is None or page in trail:
            paths.append(trail)
            return
        trail = [*trail, page]
        if page.choice is not None:
            for option in page.choice.options:
                walk(by_id.get(option.next_page), trail)
            return
        if page.next_page is None:
            paths.append(trail)
            return
        walk(by_id.get(page.next_page), trail)

    walk(entry, [])
    return paths


def _check_pages(story: Story) -> list[ContentViolation]:
    """Per-page limits (words with labels, sentence cap), unchanged by shape."""
    violations: list[ContentViolation] = []
    for page in story.pages:
        count = page_word_count(page)
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
    return violations


def _check_linear(story: Story) -> list[ContentViolation]:
    """Whole-story count and total-word limits for a straight-line story."""
    violations: list[ContentViolation] = []

    if len(story.pages) != PAGE_COUNT:
        violations.append(
            ContentViolation(
                rule="page_count",
                detail=f"story has {len(story.pages)} pages; exactly {PAGE_COUNT} required",
            )
        )

    if any(page.choice is not None for page in story.pages):
        violations.append(
            ContentViolation(
                rule="branch_structure",
                detail="linear story must not contain a choice page",
            )
        )

    total_words = sum(page_word_count(page) for page in story.pages)
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


def _check_branching(story: Story) -> list[ContentViolation]:
    """Per-path structure, length, and total-word limits for a branching story."""
    violations: list[ContentViolation] = []

    for page in story.pages:
        if page.choice is not None and page.next_page is not None:
            violations.append(
                ContentViolation(
                    rule="branch_structure",
                    page_id=page.id,
                    detail=f"choice page {page.id} must have next_page=None",
                )
            )

    if not any(page.choice is not None for page in story.pages):
        violations.append(
            ContentViolation(
                rule="branch_structure",
                detail="branching story must contain at least one choice page",
            )
        )

    paths = heard_paths(story)
    reachable = {page.id for path in paths for page in path}
    for page in story.pages:
        if page.id not in reachable:
            violations.append(
                ContentViolation(
                    rule="branch_structure",
                    page_id=page.id,
                    detail=f"page {page.id} is unreachable from any heard path",
                )
            )

    for path in paths:
        terminal = path[-1].id if path else "<empty>"
        if len(path) != PAGE_COUNT:
            violations.append(
                ContentViolation(
                    rule="path_length",
                    detail=(
                        f"heard path ending at {terminal} has {len(path)} pages; "
                        f"exactly {PAGE_COUNT} required"
                    ),
                )
            )
        path_words = sum(page_word_count(page) for page in path)
        if not STORY_WORDS_MIN <= path_words <= STORY_WORDS_MAX:
            violations.append(
                ContentViolation(
                    rule="story_words",
                    detail=(
                        f"heard path ending at {terminal} has {path_words} words; "
                        f"{STORY_WORDS_MIN}-{STORY_WORDS_MAX} required"
                    ),
                )
            )

    return violations


def check_story(story: Story) -> list[ContentViolation]:
    """Every content-limit violation in the story, or [] when it conforms."""
    violations = _check_pages(story)
    if story.shape == "branching":
        violations.extend(_check_branching(story))
    else:
        violations.extend(_check_linear(story))
    return violations
