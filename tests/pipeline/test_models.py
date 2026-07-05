"""Behavior specs for the initial story.json models — structure only.

Content-rule validation (word counts, sentence caps) lands in assemble (AI-361).
Safety rule names mirror the bold rows of docs/product.md "Safety"
(**Mildest peril only** ... **Nothing real**).
"""

import pytest
from pydantic import ValidationError

from src.pipeline.models import (
    SAFETY_RULES,
    ChoiceOption,
    ChoicePoint,
    Page,
    SafetyReport,
    SafetyVerdict,
    Story,
    WordTiming,
)


def _page(page_id: str, next_page: str | None = None) -> Page:
    return Page(id=page_id, text="La barchetta dondola.", next_page=next_page)


def test_a_linear_story_survives_the_json_round_trip_unchanged() -> None:
    """Given a valid linear story with a gloss map,
    When it is dumped to JSON and validated back,
    Then the reconstructed story equals the original — story.json is the contract.
    """
    story = Story(
        id="la-barchetta-e-la-luna",
        language="it",
        title="La barchetta e la luna",
        theme="the_sleepy_sea",
        shape="linear",
        pages=[_page("p1", "p2"), _page("p2")],
        gloss={"barchetta": "little boat"},
    )
    assert Story.model_validate_json(story.model_dump_json()) == story


def test_a_word_timing_that_ends_before_it_starts_is_rejected() -> None:
    """Given karaoke word timings (product.md **Karaoke highlighting**),
    When end_s precedes start_s,
    Then validation fails — malformed timings never reach the player.
    """
    with pytest.raises(ValidationError):
        WordTiming(word="luna", start_s=2.0, end_s=1.0)


def test_a_choice_point_requires_exactly_two_picture_options() -> None:
    """Given the picture-choice pattern (product.md **Picture choices**: two options),
    When a choice point is built with only one option,
    Then validation fails.
    """
    option = ChoiceOption(label="la baia", card_image=None, next_page="p5a")
    with pytest.raises(ValidationError):
        ChoicePoint(options=(option,))  # type: ignore[arg-type]


def test_a_story_in_an_unlocked_language_is_rejected() -> None:
    """Given the locked language set it/es/en/el/de (product.md **5 languages**),
    When a story claims language "fr",
    Then validation fails.
    """
    with pytest.raises(ValidationError):
        Story(
            id="x",
            language="fr",  # type: ignore[arg-type]
            title="X",
            theme="the_sleepy_sea",
            shape="linear",
            pages=[_page("p1")],
        )


def test_a_safety_report_must_cover_all_nine_rules_exactly_once() -> None:
    """Given the nine safety rules of product.md "Safety",
    When a report carries a verdict for every rule, Then it validates and passes;
    When any rule's verdict is missing, Then validation fails.
    """
    verdicts = [SafetyVerdict(rule=rule, passed=True, reason="ok") for rule in SAFETY_RULES]
    report = SafetyReport(verdicts=verdicts)
    assert report.passed

    with pytest.raises(ValidationError):
        SafetyReport(verdicts=verdicts[:-1])


def test_a_safety_report_fails_when_any_single_rule_fails() -> None:
    """Given verdicts where only **No brands** fails,
    When the report's overall outcome is read,
    Then it is not passed — publishing requires a unanimous pass.
    """
    verdicts = [
        SafetyVerdict(rule=rule, passed=rule != "no_brands", reason="ok") for rule in SAFETY_RULES
    ]
    assert not SafetyReport(verdicts=verdicts).passed
