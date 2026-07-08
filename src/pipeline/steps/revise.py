"""revise: targeted rewrite on failure; two failed revisions reject the story.

docs/architecture.md "Why no framework": the loop below is the pipeline's
one bounded retry — a plain `while`, not a graph runtime. docs/product.md
"Safety" enforcement: any fail routes to revise; two fails reject. Failures
come from two gates: content limits as code (content_rules) and the
cross-family safety judge.
"""

from collections.abc import Sequence

from pydantic_ai import Agent
from pydantic_ai.models import Model

from src.config import Settings
from src.pipeline.cache import ArtifactCache, run_step
from src.pipeline.content_rules import check_story
from src.pipeline.models import Language, SafetyReport, Story, Theme
from src.pipeline.providers import build_model
from src.pipeline.steps.safety import safety_gate
from src.pipeline.steps.write import (
    WRITE_INSTRUCTIONS,
    StoryDraft,
    story_from_draft,
    write_story,
)

MAX_REVISIONS = 2

# Bump when the instructions change: prompt text is a cache-key input by proxy.
PROMPT_VERSION = 1

REVISE_INSTRUCTIONS = (
    WRITE_INSTRUCTIONS
    + """
You are revising an existing story that failed review. Fix ONLY what the
listed failures name; keep everything that already works — same title
unless a failure demands otherwise, same scenes, same tone. Return the
complete corrected story.
"""
)


class StoryRejectedError(Exception):
    """Raised when a story still fails its gates after MAX_REVISIONS rewrites."""

    def __init__(self, story: Story, failures: Sequence[str]) -> None:
        self.story = story
        self.failures = list(failures)
        super().__init__(
            f"story {story.id} rejected after {MAX_REVISIONS} failed revisions: "
            + "; ".join(self.failures)
        )


def build_revise_agent(model: Model) -> Agent[None, StoryDraft]:
    return Agent(model=model, output_type=StoryDraft, instructions=REVISE_INSTRUCTIONS)


def revise_story(
    story: Story,
    failures: Sequence[str],
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
) -> Story:
    """One targeted rewrite of a failed story; unchanged inputs, zero API calls."""
    llm = model if model is not None else build_model(settings.write_model, settings)
    inputs = {
        "story": story.model_dump(mode="json"),
        "failures": list(failures),
        "model": settings.write_model,
        "prompt_version": PROMPT_VERSION,
    }

    def produce() -> bytes:
        failure_lines = "\n".join(f"- {failure}" for failure in failures)
        prompt = (
            f"This story failed review:\n{story.model_dump_json()}\n\n"
            f"Failures to fix:\n{failure_lines}"
        )
        draft = build_revise_agent(llm).run_sync(prompt).output
        revised = story_from_draft(
            draft, story_id=story.id, theme=story.theme, language=story.language
        )
        return revised.model_dump_json().encode()

    return Story.model_validate_json(run_step(cache, "revise", inputs, produce))


def _gate_failures(story: Story, report: SafetyReport) -> list[str]:
    """Everything wrong with this story, named precisely for a targeted rewrite."""
    failures = [
        f"content_rules/{violation.rule}: {violation.detail}" for violation in check_story(story)
    ]
    failures.extend(
        f"safety/{verdict.rule}: {verdict.reason}"
        for verdict in report.verdicts
        if not verdict.passed
    )
    return failures


def author_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    write_model: Model | None = None,
    safety_model: Model | None = None,
    revise_model: Model | None = None,
    premise: str | None = None,
) -> tuple[Story, SafetyReport]:
    """write → gate → bounded revise: the one loop the pipeline owns.

    Every candidate — the original and each revision — must clear both the
    content limits (as code) and all nine safety verdicts. Two failed
    revisions reject the story.
    """
    story = write_story(theme, language, settings, cache, model=write_model, premise=premise)
    report = safety_gate(story, settings, cache, model=safety_model)
    failures = _gate_failures(story, report)

    revisions = 0
    while failures and revisions < MAX_REVISIONS:
        story = revise_story(story, failures, settings, cache, model=revise_model)
        revisions += 1
        report = safety_gate(story, settings, cache, model=safety_model)
        failures = _gate_failures(story, report)

    if failures:
        raise StoryRejectedError(story, failures)
    return story, report
