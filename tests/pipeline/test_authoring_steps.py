"""Behavior specs for the authoring steps: write → safety gate → revise.

docs/architecture.md "The Authoring Pipeline": a batch job, not an agent —
linear steps, one bounded loop, artifacts on disk. docs/product.md "Safety"
enforcement: the safety node verdicts each story per rule at temperature 0;
any fail routes to revise; two fails reject.

All model traffic is mocked with pydantic-ai TestModel/FunctionModel — zero
network. The cross-family writer/judge invariant is proven by config itself:
see tests/test_config.py
(test_safety_judge_defaults_to_a_different_model_family_than_the_writer and
test_same_family_writer_and_judge_is_refused_outright) — not duplicated here.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.content_rules import check_story
from src.pipeline.models import SAFETY_RULES, Story
from src.pipeline.steps.revise import MAX_REVISIONS, StoryRejectedError, author_story
from src.pipeline.steps.safety import SAFETY_MODEL_SETTINGS, safety_gate
from src.pipeline.steps.write import StoryDraft, story_from_draft, write_story

# Five words per sentence; eight sentences per page; eight pages = 320 words.
SENTENCE = "The water sings {word} {word}."


def good_draft(word: str = "shh") -> StoryDraft:
    page = " ".join([SENTENCE.format(word=word)] * 8)
    return StoryDraft(title="La barchetta", pages=[page] * 8)


def draft_with_long_sentence() -> StoryDraft:
    """A draft whose page 1 breaks the 12-word sentence cap."""
    draft = good_draft()
    long_sentence = "The little boat rocks and rocks and rocks and rocks and rocks tonight."
    pages = list(draft.pages)
    pages[0] = f"{long_sentence} " + " ".join([SENTENCE.format(word="shh")] * 4)
    return StoryDraft(title=draft.title, pages=pages)


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))


def _cache(tmp_path: Path) -> ArtifactCache:
    return ArtifactCache(tmp_path / "story")


def _output_call(info: AgentInfo, args: dict[str, object]) -> ModelResponse:
    assert info.output_tools, "structured output expected"
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


class DraftModel(FunctionModel):
    """A writer/reviser double: returns queued drafts, counts calls."""

    def __init__(self, *drafts: StoryDraft) -> None:
        self.calls = 0
        self.seen_prompts: list[str] = []
        queue = list(drafts)

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            self.seen_prompts.append(str(messages))
            draft = queue.pop(0) if len(queue) > 1 else queue[0]
            return _output_call(info, draft.model_dump())

        super().__init__(respond)


def report_args(failing: dict[str, str] | None = None) -> dict[str, object]:
    failing = failing or {}
    return {
        "verdicts": [
            {
                "rule": rule,
                "passed": rule not in failing,
                "reason": failing.get(rule, "ok"),
            }
            for rule in SAFETY_RULES
        ]
    }


class JudgeModel(FunctionModel):
    """A safety-judge double: emits queued nine-rule reports, records settings."""

    def __init__(self, *reports: dict[str, object]) -> None:
        self.calls = 0
        self.seen_temperatures: list[object] = []
        self.seen_prompts: list[str] = []
        queue = list(reports)

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            settings = info.model_settings or {}
            self.seen_temperatures.append(settings.get("temperature"))
            self.seen_prompts.append(str(messages))
            report = queue.pop(0) if len(queue) > 1 else queue[0]
            return _output_call(info, report)

        super().__init__(respond)


# --- write ------------------------------------------------------------------


def test_write_authors_a_typed_linear_story_that_passes_the_content_rules(
    tmp_path: Path,
) -> None:
    """Given a locked theme and language,
    When the write step runs against a model returning a conforming draft,
    Then it yields a typed linear Story in that language, eight chained
    pages, and zero content-rule violations (product.md "Content Rules").
    """
    model = TestModel(custom_output_args=good_draft().model_dump())
    story = write_story("the_sleepy_sea", "it", _settings(), _cache(tmp_path), model=model)

    assert isinstance(story, Story)
    assert story.language == "it"
    assert story.theme == "the_sleepy_sea"
    assert story.shape == "linear"
    assert [page.id for page in story.pages] == [f"p{i}" for i in range(1, 9)]
    assert [page.next_page for page in story.pages] == [
        "p2",
        "p3",
        "p4",
        "p5",
        "p6",
        "p7",
        "p8",
        None,
    ]
    assert check_story(story) == []


def test_rerunning_write_with_unchanged_inputs_makes_zero_model_calls(
    tmp_path: Path,
) -> None:
    """Given a written story already persisted for these inputs,
    When write runs again with unchanged inputs,
    Then the model is never invoked — the artifact is served from disk
    (docs/architecture.md "Content-addressed caching").
    """
    model = DraftModel(good_draft())
    cache = _cache(tmp_path)
    first = write_story("the_sleepy_sea", "it", _settings(), cache, model=model)
    second = write_story("the_sleepy_sea", "it", _settings(), cache, model=model)

    assert first == second
    assert model.calls == 1


# --- safety gate --------------------------------------------------------------


def _story(draft: StoryDraft | None = None, story_id: str = "story-1") -> Story:
    return story_from_draft(
        draft or good_draft(), story_id=story_id, theme="the_sleepy_sea", language="it"
    )


def test_the_safety_gate_verdicts_all_nine_rules_at_temperature_zero(
    tmp_path: Path,
) -> None:
    """Given a written story,
    When the safety gate judges it (product.md "Safety" enforcement: the
    safety node verdicts each story per rule at temperature 0),
    Then the typed report covers all nine rules and the model call carried
    temperature 0 — asserted on the settings the model actually received.
    """
    judge = JudgeModel(report_args())
    report = safety_gate(_story(), _settings(), _cache(tmp_path), model=judge)

    assert sorted(v.rule for v in report.verdicts) == sorted(SAFETY_RULES)
    assert report.passed
    assert judge.seen_temperatures == [0.0]
    assert SAFETY_MODEL_SETTINGS["temperature"] == 0.0


def test_a_failed_verdict_names_its_rule_and_reason(tmp_path: Path) -> None:
    """Given a judge that fails one rule,
    When the safety gate reports,
    Then the report fails overall and the failing verdict carries the rule
    and the judge's reason — precise enough to drive a targeted revise.
    """
    judge = JudgeModel(report_args({"no_brands": "page 3 names a licensed character"}))
    report = safety_gate(_story(), _settings(), _cache(tmp_path), model=judge)

    assert not report.passed
    failed = [v for v in report.verdicts if not v.passed]
    assert [(v.rule, v.reason) for v in failed] == [
        ("no_brands", "page 3 names a licensed character")
    ]


def test_rerunning_the_gate_on_an_unchanged_story_makes_zero_model_calls(
    tmp_path: Path,
) -> None:
    """Given a safety report already persisted for this exact story,
    When the gate runs again on the unchanged story,
    Then the judge is never invoked — the verdict is served from disk.
    """
    judge = JudgeModel(report_args())
    cache = _cache(tmp_path)
    first = safety_gate(_story(), _settings(), cache, model=judge)
    second = safety_gate(_story(), _settings(), cache, model=judge)

    assert first == second
    assert judge.calls == 1


# --- revise and the bounded loop ----------------------------------------------


def test_a_safe_story_passes_the_gate_without_any_revision(tmp_path: Path) -> None:
    """Given a writer whose story passes every content limit and safety rule,
    When the authoring loop runs,
    Then the written story comes back with a passing report and the reviser
    is never invoked — the all-pass path goes straight through.
    """
    writer = DraftModel(good_draft())
    judge = JudgeModel(report_args())
    reviser = DraftModel(good_draft("hush"))

    story, report = author_story(
        "the_sleepy_sea",
        "it",
        _settings(),
        _cache(tmp_path),
        write_model=writer,
        safety_model=judge,
        revise_model=reviser,
    )

    assert report.passed
    assert "shh" in story.pages[0].text
    assert writer.calls == 1
    assert judge.calls == 1
    assert reviser.calls == 0


def test_a_failed_safety_verdict_routes_to_a_targeted_revise_then_passes(
    tmp_path: Path,
) -> None:
    """Given a judge that fails one rule on the first pass and passes the
    revision (product.md "Safety": any fail routes to revise),
    When the authoring loop runs,
    Then exactly one revision happens, the reviser is shown the failing
    rule's reason (targeted rewrite), and the revised story is returned.
    """
    writer = DraftModel(good_draft())
    judge = JudgeModel(
        report_args({"no_brands": "page 2 names a licensed character"}),
        report_args(),
    )
    reviser = DraftModel(good_draft("hush"))

    story, report = author_story(
        "the_sleepy_sea",
        "it",
        _settings(),
        _cache(tmp_path),
        write_model=writer,
        safety_model=judge,
        revise_model=reviser,
    )

    assert report.passed
    assert "hush" in story.pages[0].text  # the revised story, not the original
    assert reviser.calls == 1
    assert judge.calls == 2
    assert "page 2 names a licensed character" in reviser.seen_prompts[0]


def test_two_failed_revisions_reject_the_story(tmp_path: Path) -> None:
    """Given a judge that keeps failing a rule no matter the rewrite
    (product.md "Safety": two fails reject),
    When the authoring loop runs,
    Then after exactly two failed revisions the story is rejected with the
    surviving failures named — a bounded loop, never an unbounded retry.
    """
    writer = DraftModel(good_draft())
    judge = JudgeModel(report_args({"no_fear_reinforcement": "page 5 uses darkness as a threat"}))
    reviser = DraftModel(good_draft("hush"), good_draft("plum"))

    with pytest.raises(StoryRejectedError) as excinfo:
        author_story(
            "the_sleepy_sea",
            "it",
            _settings(),
            _cache(tmp_path),
            write_model=writer,
            safety_model=judge,
            revise_model=reviser,
        )

    assert reviser.calls == MAX_REVISIONS == 2
    assert judge.calls == 3  # the original and both revisions were each judged
    assert "no_fear_reinforcement" in str(excinfo.value)


def test_a_story_violating_a_content_limit_routes_to_revise_even_when_the_judge_passes_it(
    tmp_path: Path,
) -> None:
    """Given a writer that breaks the 12-word sentence cap while the judge
    passes everything,
    When the authoring loop runs,
    Then code validation — not prompt hope — routes the story to revise with
    the violation named, and the conforming revision is returned.
    """
    writer = DraftModel(draft_with_long_sentence())
    judge = JudgeModel(report_args())
    reviser = DraftModel(good_draft("hush"))

    story, report = author_story(
        "the_sleepy_sea",
        "it",
        _settings(),
        _cache(tmp_path),
        write_model=writer,
        safety_model=judge,
        revise_model=reviser,
    )

    assert report.passed
    assert check_story(story) == []
    assert reviser.calls == 1
    assert "sentence" in reviser.seen_prompts[0]  # the violation reached the reviser
