"""write: native-language story authoring on the strong model.

docs/architecture.md "Model roles": content rules are embedded in the
prompt, and stories are authored natively per language — never translated.
The prompt is hope; src/pipeline/content_rules.py is the validation.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from src.config import Settings
from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.content_rules import (
    PAGE_COUNT,
    PAGE_WORDS_MAX,
    PAGE_WORDS_MIN,
    SENTENCE_WORDS_MAX,
    STORY_WORDS_MAX,
    STORY_WORDS_MIN,
)
from src.pipeline.models import Language, Page, Story, Theme
from src.pipeline.providers import build_model

# Bump when the instructions change: prompt text is a cache-key input by proxy.
PROMPT_VERSION = 1

LANGUAGE_NAMES: dict[Language, str] = {
    "it": "Italian",
    "es": "Spanish",
    "en": "English",
    "el": "Greek",
    "de": "German",
}

# The content rules from docs/product.md "Content Rules", verbatim as limits.
WRITE_INSTRUCTIONS = f"""\
You are a warm bedtime storyteller for pre-readers aged 3-6, in the craft of
the Italian cantastorie. Write the story natively in the requested language —
never write in English and translate.

Hard limits (each is validated by code after you answer):
- Exactly {PAGE_COUNT} pages.
- {PAGE_WORDS_MIN}-{PAGE_WORDS_MAX} words per page; {STORY_WORDS_MIN}-{STORY_WORDS_MAX} words in total.
- No sentence longer than {SENTENCE_WORDS_MAX} words.

Style:
- Present tense preferred; gentle repetition and sound words.
- The final page lands on comfort or sleepiness — a bedtime wind-down.
- Only the mildest peril; no darkness-as-threat, monsters, abandonment,
  or injury; no brands or licensed characters; no romance; kind, inclusive
  characters; resolution through help, never punishment; no real people,
  real places presented as real, or religious instruction.
"""


class StoryDraft(BaseModel):
    """What the writer model returns: a title and the pages' prose, in order."""

    title: str
    pages: list[str] = Field(min_length=1)


def build_write_agent(model: Model) -> Agent[None, StoryDraft]:
    return Agent(model=model, output_type=StoryDraft, instructions=WRITE_INSTRUCTIONS)


def story_from_draft(
    draft: StoryDraft, *, story_id: str, theme: Theme, language: Language
) -> Story:
    """Assemble the typed linear Story around the model's prose."""
    last = len(draft.pages)
    pages = [
        Page(id=f"p{i}", text=text, next_page=f"p{i + 1}" if i < last else None)
        for i, text in enumerate(draft.pages, start=1)
    ]
    return Story(
        id=story_id,
        language=language,
        title=draft.title,
        theme=theme,
        shape="linear",
        pages=pages,
    )


def write_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
) -> Story:
    """Author a native-language story; unchanged inputs cost zero API calls."""
    llm = model if model is not None else build_model(settings.write_model, settings)
    inputs = {
        "theme": theme,
        "language": language,
        "model": settings.write_model,
        "prompt_version": PROMPT_VERSION,
    }
    story_id = f"{theme.replace('_', '-')}-{language}-{cache_key(inputs)[:8]}"

    def produce() -> bytes:
        prompt = (
            f"Write a bedtime story in {LANGUAGE_NAMES[language]} "
            f"on the theme: {theme.replace('_', ' ')}."
        )
        draft = build_write_agent(llm).run_sync(prompt).output
        story = story_from_draft(draft, story_id=story_id, theme=theme, language=language)
        return story.model_dump_json().encode()

    return Story.model_validate_json(run_step(cache, "write", inputs, produce))
