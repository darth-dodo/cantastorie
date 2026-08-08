"""Behavior specs for the branching writer: draft model and typed builder.

docs/product.md "Content Rules" (**Branching stories**): a shared prefix
forks once into two arms; every heard path must respect every linear limit.
The prompt carries the rules; src/pipeline/content_rules.py decides. Page ids
are deterministic (p1.., a1.., b1..) so later tasks can address them.
"""

from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT, check_story
from src.pipeline.steps.write import BranchingStoryDraft, branching_story_from_draft

SHARED = PAGE_COUNT - ARM_PAGES
PAGE_TEXT = " ".join(["dorme."] * 30)


def make_draft() -> BranchingStoryDraft:
    return BranchingStoryDraft(
        title="La lanterna e la barchetta",
        shared_pages=[PAGE_TEXT] * SHARED,
        option_labels=("la lanterna", "la barchetta"),
        arm_a=[PAGE_TEXT] * ARM_PAGES,
        arm_b=[PAGE_TEXT] * ARM_PAGES,
    )


def test_draft_builds_a_valid_branching_story():
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    assert story.shape == "branching"
    assert check_story(story) == []


def test_choice_sits_on_the_last_shared_page():
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    choice_page = next(p for p in story.pages if p.choice is not None)
    assert choice_page.id == f"p{SHARED}"
    assert choice_page.next_page is None
    assert choice_page.choice.options[0].next_page == "a1"
    assert choice_page.choice.options[1].next_page == "b1"
