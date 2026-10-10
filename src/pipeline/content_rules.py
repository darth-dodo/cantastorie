"""Content limits as code, not prompt hope.

docs/product.md "Content Rules" (**Linear stories**): 10 pages, 30-70 words
per page, 250-600 total, a 20-word sentence cap. Choice labels count as
story text for every limit (**Branching stories**). The writer's prompt
carries the same rules, but only these pure functions decide.

Japanese does not separate words with spaces, so it is measured in characters
(spaces and punctuation excluded) against the word limits scaled by
CHARACTERS_PER_WORD (AI-508). Every other roster language counts words.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from src.pipeline.models import Language, Page, Story

PAGE_COUNT = 10
PAGE_WORDS_MIN = 30
PAGE_WORDS_MAX = 70
STORY_WORDS_MIN = 250
STORY_WORDS_MAX = 600
SENTENCE_WORDS_MAX = 20
ARM_PAGES = 4  # pages per branch arm; shared prefix = PAGE_COUNT - ARM_PAGES

# Characters of Japanese story text that stand in for one English word. A
# children's register leans on kana, so it runs longer than adult prose.
CHARACTERS_PER_WORD = 2.5

LengthUnit = Literal["words", "characters"]


@dataclass(frozen=True)
class LengthLimits:
    """One language's length limits, all in the same unit."""

    unit: LengthUnit
    page_min: int
    page_max: int
    story_min: int
    story_max: int
    sentence_max: int


WORD_LIMITS = LengthLimits(
    unit="words",
    page_min=PAGE_WORDS_MIN,
    page_max=PAGE_WORDS_MAX,
    story_min=STORY_WORDS_MIN,
    story_max=STORY_WORDS_MAX,
    sentence_max=SENTENCE_WORDS_MAX,
)


def _scaled(words: int) -> int:
    return round(words * CHARACTERS_PER_WORD)


CHARACTER_LIMITS = LengthLimits(
    unit="characters",
    page_min=_scaled(PAGE_WORDS_MIN),
    page_max=_scaled(PAGE_WORDS_MAX),
    story_min=_scaled(STORY_WORDS_MIN),
    story_max=_scaled(STORY_WORDS_MAX),
    sentence_max=_scaled(SENTENCE_WORDS_MAX),
)

# Languages written without spaces between words.
CHARACTER_LANGUAGES: frozenset[Language] = frozenset({"ja"})

ContentRule = Literal[
    "page_count",
    "page_words",
    "story_words",
    "sentence_cap",
    "branch_structure",
    "path_length",
]

# A sentence ends on . ! ? … or the Devanagari danda (U+0964) followed by a
# space, or on a Japanese full stop, exclamation or question mark (U+3002,
# U+FF01, U+FF1F) with no space needed. A closing bracket or quote after a
# Japanese stop (U+300D, U+300F, U+FF09) stays with its sentence.
_JA_STOPS = "".join(map(chr, (0x3002, 0xFF01, 0xFF1F)))
_JA_CLOSERS = "".join(map(chr, (0x300D, 0x300F, 0xFF09)))
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?…।])\s+"
    rf"|(?<=[{_JA_STOPS}])(?![{_JA_CLOSERS}])\s*"
    rf"|(?<=[{_JA_STOPS}][{_JA_CLOSERS}])\s*"
)


class ContentViolation(BaseModel):
    """One broken content limit, precise enough to drive a targeted revise."""

    rule: ContentRule
    page_id: str | None = None
    detail: str


def words(text: str) -> list[str]:
    return text.split()


def sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BOUNDARY.split(text.strip()) if part]


def limits_for(language: Language) -> LengthLimits:
    return CHARACTER_LIMITS if language in CHARACTER_LANGUAGES else WORD_LIMITS


def text_length(text: str, language: Language) -> int:
    """Length in the language's unit: words, or characters without spaces and punctuation."""
    if language in CHARACTER_LANGUAGES:
        return sum(
            1
            for char in text
            if not char.isspace() and not unicodedata.category(char).startswith("P")
        )
    return len(words(text))


def length_note(language: Language) -> str:
    """The length rules restated in characters, for prompts; empty for word languages."""
    if language not in CHARACTER_LANGUAGES:
        return ""
    limits = limits_for(language)
    return (
        "Length is measured in characters for this language, not words "
        "(spaces and punctuation do not count): "
        f"{limits.page_min}-{limits.page_max} characters per page; "
        f"{limits.story_min}-{limits.story_max} characters in total; "
        f"no sentence over {limits.sentence_max} characters."
    )


def page_word_count(page: Page, language: Language = "en") -> int:
    """Length of a page in the language's unit; choice labels count as story text."""
    count = text_length(page.text, language)
    if page.choice is not None:
        count += sum(text_length(option.label, language) for option in page.choice.options)
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
    """Per-page limits (length with labels, sentence cap), unchanged by shape."""
    violations: list[ContentViolation] = []
    limits = limits_for(story.language)
    unit = limits.unit
    for page in story.pages:
        count = page_word_count(page, story.language)
        if not limits.page_min <= count <= limits.page_max:
            violations.append(
                ContentViolation(
                    rule="page_words",
                    page_id=page.id,
                    detail=(
                        f"page {page.id} has {count} {unit}; "
                        f"{limits.page_min}-{limits.page_max} required"
                    ),
                )
            )
        for sentence in _page_sentences(page):
            length = text_length(sentence, story.language)
            if length > limits.sentence_max:
                violations.append(
                    ContentViolation(
                        rule="sentence_cap",
                        page_id=page.id,
                        detail=(
                            f"page {page.id} sentence has {length} {unit}, over the "
                            f"{limits.sentence_max}-{unit[:-1]} cap: {sentence!r}"
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

    limits = limits_for(story.language)
    total = sum(page_word_count(page, story.language) for page in story.pages)
    if not limits.story_min <= total <= limits.story_max:
        violations.append(
            ContentViolation(
                rule="story_words",
                detail=(
                    f"story has {total} {limits.unit}; "
                    f"{limits.story_min}-{limits.story_max} required"
                ),
            )
        )

    return violations


def _check_branching(story: Story) -> list[ContentViolation]:
    """Per-path structure, length, and total-length limits for a branching story."""
    violations: list[ContentViolation] = []
    limits = limits_for(story.language)

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
        path_length = sum(page_word_count(page, story.language) for page in path)
        if not limits.story_min <= path_length <= limits.story_max:
            violations.append(
                ContentViolation(
                    rule="story_words",
                    detail=(
                        f"heard path ending at {terminal} has {path_length} {limits.unit}; "
                        f"{limits.story_min}-{limits.story_max} required"
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
