"""Behavior spec: the safety gate judges branch arms and choice labels.

docs/product.md "Safety" enforcement: the safety node verdicts each story per
rule at temperature 0. For a branching story that means the judge must see
*every* heard path — both arm's prose and both choice labels — not just the
shared prefix; a blind arm is exactly the failure mode that matters.

The gate serializes the whole Story for the judge (safety.py:75,
``story.model_dump_json()``), so choice labels and every arm page reach the
prompt. This test pins that as a regression guarantee.

All model traffic is mocked with a recording FunctionModel — zero network,
mirroring the JudgeModel double in tests/pipeline/test_authoring_steps.py.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.content_rules import ARM_PAGES, PAGE_COUNT
from src.pipeline.models import SAFETY_RULES, Story
from src.pipeline.steps.safety import safety_gate
from src.pipeline.steps.write import BranchingStoryDraft, branching_story_from_draft

SHARED = PAGE_COUNT - ARM_PAGES
OPTION_LABELS = ("la lanterna", "la barchetta")


def _page_text(page_id: str) -> str:
    """Distinct prose per page so containment assertions are meaningful.

    The page id opens the first sentence; the rest is filler that keeps every
    page inside the 30-70 word limit with no sentence over the 20-word cap.
    """
    return f"pagina {page_id} dorme. " + " ".join(["dorme."] * 30)


def make_draft() -> BranchingStoryDraft:
    return BranchingStoryDraft(
        title="La lanterna e la barchetta",
        shared_pages=[_page_text(f"p{i}") for i in range(1, SHARED + 1)],
        option_labels=OPTION_LABELS,
        arm_a=[_page_text(f"a{i}") for i in range(1, ARM_PAGES + 1)],
        arm_b=[_page_text(f"b{i}") for i in range(1, ARM_PAGES + 1)],
    )


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key=SecretStr("sk-or-test"))


def _cache(tmp_path: Path) -> ArtifactCache:
    return ArtifactCache(tmp_path / "story")


class RecordingJudge(FunctionModel):
    """A safety-judge double that records the prompt it was handed."""

    def __init__(self) -> None:
        self.last_prompt: str = ""

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.last_prompt = str(messages)
            assert info.output_tools, "structured output expected"
            args = {
                "verdicts": [
                    {"rule": rule, "passed": True, "reason": "ok"} for rule in SAFETY_RULES
                ]
            }
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)]
            )

        super().__init__(respond)


def test_judge_sees_both_arms_and_labels(tmp_path: Path) -> None:
    """Given a branching story with distinct prose on every arm page,
    When the safety gate judges it,
    Then the judge's prompt carries both choice labels and the opening
    sentence of every page on both arms — no heard path is hidden.
    """
    story = branching_story_from_draft(
        make_draft(), story_id="s1", theme="the_little_boat", language="it"
    )
    assert story.shape == "branching"

    judge = RecordingJudge()
    safety_gate(story, _settings(), _cache(tmp_path), model=judge)

    prompt = judge.last_prompt
    for label in OPTION_LABELS:
        assert label in prompt

    assert isinstance(story, Story)
    for page in story.pages:
        assert page.text.split(".")[0] in prompt
