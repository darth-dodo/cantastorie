"""generate (AI-361): the whole authoring run, write through stage.

One linear pass (docs/architecture.md "The Authoring Pipeline": a batch job, not
an agent) that turns a theme and a language into a story staged for the operator
to review: author_story → narrate → illustrate → assemble → stage, against the
story's own working folder content/{story-id}/ via the content-addressed cache.
Every step is a pure cache lookup on a re-run, so a repeated generate re-buys
nothing.

The gloss step is slice 6 and the audit is AI-378 — neither runs here. Spoken
prompts are staged only for Italian; the other languages' prompts arrive with
slice 4.

The provider seams (models, narration client, image transport) are injectable
so the whole run is exercised with zero network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.observability import typed_traceable
from src.pipeline.cache import ArtifactCache
from src.pipeline.publish import stage_story
from src.pipeline.steps.assemble import assemble_story
from src.pipeline.steps.illustrate import illustrate_story
from src.pipeline.steps.narrate import narrate_pages, synthesize_utterances
from src.pipeline.steps.revise import author_story
from src.pipeline.steps.write import derive_story_id

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from pydantic_ai.models import Model

    from src.config import Settings
    from src.pipeline.models import Language, Theme
    from src.pipeline.providers import NarrationClient


@typed_traceable(name="pipeline.generate_story")
def generate_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    *,
    write_model: Model | None = None,
    safety_model: Model | None = None,
    revise_model: Model | None = None,
    narration_client: NarrationClient | None = None,
    image_transport: httpx.BaseTransport | None = None,
    premise: str | None = None,
) -> Path:
    """Author, narrate, illustrate, assemble, and stage one story.

    Returns the staging folder the operator opens — story.json beside its
    hashed audio and images. Publishing is a separate, operator-gated step.
    """
    story_id = derive_story_id(theme, language, settings, premise)
    cache = ArtifactCache(settings.content_dir / story_id)

    story, _report = author_story(
        theme,
        language,
        settings,
        cache,
        write_model=write_model,
        safety_model=safety_model,
        revise_model=revise_model,
        premise=premise,
    )
    narrated = narrate_pages(story.pages, language, settings, cache, narration_client)
    story = story.model_copy(update={"pages": narrated})
    illustrations = illustrate_story(story, settings, cache, transport=image_transport)
    assembled = assemble_story(story, illustrations)

    if language == "it":
        synthesize_utterances(
            settings,
            cache,
            out_dir=settings.staging_dir,
            language=language,
            client=narration_client,
        )

    return stage_story(assembled, settings)
