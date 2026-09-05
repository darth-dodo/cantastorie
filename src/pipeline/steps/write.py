"""write: native-language story authoring on the strong model.

docs/architecture.md "Model roles": content rules are embedded in the
prompt, and stories are authored natively per language — never translated.
The prompt is hope; src/pipeline/content_rules.py is the validation.
"""

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from src.config import Settings
from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.content_rules import (
    ARM_PAGES,
    PAGE_COUNT,
    PAGE_WORDS_MAX,
    PAGE_WORDS_MIN,
    SENTENCE_WORDS_MAX,
    STORY_WORDS_MAX,
    STORY_WORDS_MIN,
)
from src.pipeline.models import ChoiceOption, ChoicePoint, Language, Page, Story, Theme
from src.pipeline.providers import build_model

SHARED_PAGES = PAGE_COUNT - ARM_PAGES

# Bump when the instructions change: prompt text is a cache-key input by proxy.
PROMPT_VERSION = 1

LANGUAGE_NAMES: dict[Language, str] = {
    "it": "Italian",
    "es": "Spanish",
    "en": "English",
    "el": "Greek",
    "de": "German",
    "bg": "Bulgarian",
    "ru": "Russian",
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

BRANCHING_INSTRUCTIONS = (
    WRITE_INSTRUCTIONS
    + f"""

This story BRANCHES. Structure:
- {SHARED_PAGES} shared pages; the last shared page ends at a gentle split
  (a path forks, two doors, two friends beckon — never danger).
- Two options a pre-reader can tell apart as pictures. Each label is 1-4
  words in the story language, concrete and visual (a lantern / a rowboat).
- Two arms of exactly {ARM_PAGES} pages each, continuing from the split.
  Each arm is a complete, different ending; BOTH arms land on comfort or
  sleepiness. Every heard path (shared pages plus one arm) must respect
  every limit above as if it were the whole story.
"""
)


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


class BranchingStoryDraft(BaseModel):
    """The writer's branching return: shared prose, two labeled arms."""

    title: str
    shared_pages: list[str]  # prose for p1..p{SHARED_PAGES}; the last ends at the split
    option_labels: tuple[str, str]  # short picture-card labels, story language
    arm_a: list[str]  # prose continuing option one, ARM_PAGES pages to its ending
    arm_b: list[str]  # prose continuing option two, ARM_PAGES pages to its ending


def branching_story_from_draft(
    draft: BranchingStoryDraft, *, story_id: str, theme: Theme, language: Language
) -> Story:
    """Assemble the typed branching Story around the model's prose."""
    shared = [
        Page(
            id=f"p{i}",
            text=text,
            next_page=f"p{i + 1}" if i < SHARED_PAGES else None,
        )
        for i, text in enumerate(draft.shared_pages, start=1)
    ]
    shared[-1].choice = ChoicePoint(
        options=(
            ChoiceOption(label=draft.option_labels[0], next_page="a1"),
            ChoiceOption(label=draft.option_labels[1], next_page="b1"),
        )
    )

    def arm(prefix: str, texts: list[str]) -> list[Page]:
        return [
            Page(
                id=f"{prefix}{i}",
                text=text,
                next_page=f"{prefix}{i + 1}" if i < ARM_PAGES else None,
            )
            for i, text in enumerate(texts, start=1)
        ]

    return Story(
        id=story_id,
        language=language,
        title=draft.title,
        theme=theme,
        shape="branching",
        pages=[*shared, *arm("a", draft.arm_a), *arm("b", draft.arm_b)],
    )


def _write_inputs(
    theme: Theme, language: Language, settings: Settings, premise: str | None = None
) -> dict[str, object]:
    inputs: dict[str, object] = {
        "theme": theme,
        "language": language,
        "model": settings.write_model,
        "prompt_version": PROMPT_VERSION,
    }
    # Only when present, so a no-premise run keeps its pre-premise cache key.
    if premise is not None:
        inputs["premise"] = premise
    return inputs


def derive_story_id(
    theme: Theme, language: Language, settings: Settings, premise: str | None = None
) -> str:
    """The story's stable id — a slug plus a hash of the write inputs.

    Deterministic from theme + language + writer model (+ premise when given),
    so the CLI can name the working folder content/{story-id}/ before the story
    is written.
    """
    return f"{theme.replace('_', '-')}-{language}-{cache_key(_write_inputs(theme, language, settings, premise))[:8]}"


def write_story(
    theme: Theme,
    language: Language,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
    premise: str | None = None,
    shape: Literal["linear", "branching"] = "linear",
) -> Story:
    """Author a native-language story; unchanged inputs cost zero API calls.

    An optional premise steers the plot; when given it also distinguishes the
    cache key and story id, so a premised run never reuses a plain-theme story.
    The shape joins the cache key, so a linear and a branching run of the same
    theme cache separately.
    """
    llm = model if model is not None else build_model(settings.write_model, settings)
    inputs = {**_write_inputs(theme, language, settings, premise), "shape": shape}
    story_id = derive_story_id(theme, language, settings, premise)

    def produce() -> bytes:
        prompt = (
            f"Write a bedtime story in {LANGUAGE_NAMES[language]} "
            f"on the theme: {theme.replace('_', ' ')}."
        )
        if premise is not None:
            prompt += f"\nFollow this premise closely:\n{premise}"
        if shape == "branching":
            agent = Agent(
                model=llm,
                output_type=BranchingStoryDraft,
                instructions=BRANCHING_INSTRUCTIONS,
            )
            branching_draft = agent.run_sync(prompt).output
            story = branching_story_from_draft(
                branching_draft, story_id=story_id, theme=theme, language=language
            )
        else:
            draft = build_write_agent(llm).run_sync(prompt).output
            story = story_from_draft(draft, story_id=story_id, theme=theme, language=language)
        return story.model_dump_json().encode()

    return Story.model_validate_json(run_step(cache, "write", inputs, produce))
