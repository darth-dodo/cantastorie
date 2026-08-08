"""Behavior specs for the branching-aware revise loop.

docs/architecture.md "Why no framework": revise is the pipeline's one bounded
retry. docs/product.md "Content Rules" (**Branching stories**): a revised
branching story must stay branching — same fork, same page ids, every heard
path still within every limit.

All model traffic is mocked with pydantic-ai FunctionModel — zero network,
mirroring tests/pipeline/test_authoring_steps.py's reviser double.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT, check_story
from src.pipeline.steps.revise import revise_story
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


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))


def _cache(tmp_path: Path) -> ArtifactCache:
    return ArtifactCache(tmp_path / "story")


def _output_call(info: AgentInfo, args: dict[str, object]) -> ModelResponse:
    assert info.output_tools, "structured output expected"
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


class BranchingDraftModel(FunctionModel):
    """A branching reviser double: returns queued branching drafts, counts calls."""

    def __init__(self, *drafts: BranchingStoryDraft) -> None:
        self.calls = 0
        self.seen_prompts: list[str] = []
        queue = list(drafts)

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls += 1
            self.seen_prompts.append(str(messages))
            draft = queue.pop(0) if len(queue) > 1 else queue[0]
            return _output_call(info, draft.model_dump())

        super().__init__(respond)


def test_revise_preserves_branching_structure(tmp_path: Path) -> None:
    """Given a branching story that failed review and a reviser returning a
    corrected branching draft,
    When revise_story runs,
    Then the revised story stays branching, keeps the same page-id structure,
    and clears every content rule (docs/product.md "Content Rules").
    """
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    reviser = BranchingDraftModel(make_draft())

    revised = revise_story(
        story,
        ["content_rules/sentence_cap: page a2 sentence over cap"],
        _settings(),
        _cache(tmp_path),
        model=reviser,
    )

    assert revised.shape == "branching"
    assert {p.id for p in revised.pages} == {p.id for p in story.pages}
    assert check_story(revised) == []
    assert reviser.calls == 1


def test_revise_shows_the_reviser_the_named_failure(tmp_path: Path) -> None:
    """Given a branching story routed to revise,
    When revise_story runs,
    Then the named failure reaches the reviser's prompt — a targeted rewrite.
    """
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    reviser = BranchingDraftModel(make_draft())

    revise_story(
        story,
        ["content_rules/path_length: heard path ending at b4 too short"],
        _settings(),
        _cache(tmp_path),
        model=reviser,
    )

    assert "heard path ending at b4 too short" in reviser.seen_prompts[0]


def test_rerunning_revise_on_unchanged_inputs_makes_zero_model_calls(
    tmp_path: Path,
) -> None:
    """Given a revised branching story already persisted for these inputs,
    When revise runs again with unchanged inputs,
    Then the model is never re-invoked — the artifact is served from disk
    (docs/architecture.md "Content-addressed caching").
    """
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    reviser = BranchingDraftModel(make_draft())
    cache = _cache(tmp_path)
    failures = ["content_rules/sentence_cap: page a2 sentence over cap"]

    first = revise_story(story, failures, _settings(), cache, model=reviser)
    second = revise_story(story, failures, _settings(), cache, model=reviser)

    assert first == second
    assert reviser.calls == 1
