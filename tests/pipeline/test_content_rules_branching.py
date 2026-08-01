"""Branching content rules: heard-path enumeration and per-path limits."""

from src.pipeline.content_rules import (
    ARM_PAGES,
    PAGE_COUNT,
    PAGE_WORDS_MIN,
    check_story,
    heard_paths,
)
from src.pipeline.models import ChoiceOption, ChoicePoint, Page, Story

SHARED = PAGE_COUNT - ARM_PAGES

# PAGE_WORDS_MIN short sentences of one word each satisfies every limit:
# page words >= min, sentence cap, and story totals scale with PAGE_COUNT.
PAGE_TEXT = " ".join(["dorme."] * PAGE_WORDS_MIN)


def branching_story() -> Story:
    shared = [Page(id=f"p{i}", text=PAGE_TEXT, next_page=f"p{i + 1}") for i in range(1, SHARED)]
    choice_page = Page(
        id=f"p{SHARED}",
        text=PAGE_TEXT,
        next_page=None,
        choice=ChoicePoint(
            options=(
                ChoiceOption(label="la lanterna", next_page="a1"),
                ChoiceOption(label="la barchetta", next_page="b1"),
            )
        ),
    )
    arm_a = [
        Page(
            id=f"a{i}",
            text=PAGE_TEXT,
            next_page=f"a{i + 1}" if i < ARM_PAGES else None,
        )
        for i in range(1, ARM_PAGES + 1)
    ]
    arm_b = [
        Page(
            id=f"b{i}",
            text=PAGE_TEXT,
            next_page=f"b{i + 1}" if i < ARM_PAGES else None,
        )
        for i in range(1, ARM_PAGES + 1)
    ]
    return Story(
        id="test-branching",
        language="it",
        title="La prova",
        theme="the_little_boat",
        shape="branching",
        pages=[*shared, choice_page, *arm_a, *arm_b],
    )


def test_heard_paths_enumerates_both_arms():
    paths = heard_paths(branching_story())
    assert len(paths) == 2
    assert all(len(path) == PAGE_COUNT for path in paths)
    assert paths[0][-1].id == f"a{ARM_PAGES}"
    assert paths[1][-1].id == f"b{ARM_PAGES}"


def test_valid_branching_story_passes():
    assert check_story(branching_story()) == []


def test_short_arm_is_a_path_length_violation():
    story = branching_story()
    story = story.model_copy(update={"pages": [p for p in story.pages if p.id != f"a{ARM_PAGES}"]})
    # a(ARM_PAGES-1) now dangles: fix its link so only the length breaks
    for page in story.pages:
        if page.id == f"a{ARM_PAGES - 1}":
            page.next_page = None
    violations = check_story(story)
    assert any(v.rule == "path_length" for v in violations)


def test_choice_page_with_next_page_is_a_structure_violation():
    story = branching_story()
    for page in story.pages:
        if page.choice is not None:
            page.next_page = "a1"
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)


def test_unreachable_page_is_a_structure_violation():
    story = branching_story()
    orphan = Page(id="zz", text=PAGE_TEXT, next_page=None)
    story = story.model_copy(update={"pages": [*story.pages, orphan]})
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)


def test_linear_story_with_a_choice_is_a_structure_violation():
    story = branching_story().model_copy(update={"shape": "linear"})
    violations = check_story(story)
    assert any(v.rule == "branch_structure" for v in violations)
