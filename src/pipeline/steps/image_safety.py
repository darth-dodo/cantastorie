"""image safety: a cross-family vision judge over every rendered image (B4).

**Calm pictures** (docs/product.md "Safety") can only be judged once the
pictures exist, so it runs here, after illustrate, not in the text gate.
Every image a child sees — each page, each choice card, the cover — goes to
a vision model on OpenRouter as a base64 PNG and is verdicted at temperature
0 on three criteria: no text, nothing frightening, calm. The character sheet
is a reference input only and never ships, so it is not judged.

A failing image is redrawn (a new cache key per redraw) up to
IMAGE_SAFETY_MAX_REGENERATIONS times; still failing, the story is rejected.
Verdicts are cached on the image bytes, so an unchanged image costs zero
judge calls. The vision judge's family must differ from the image model's —
Settings refuses the config otherwise (docs/architecture.md "Model roles").
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from src.config import Settings
from src.pipeline.cache import ArtifactCache, run_step
from src.pipeline.models import ImageSafetyReport, Story
from src.pipeline.providers import build_model
from src.pipeline.steps.illustrate import (
    COVER_SLOT,
    IllustrationSet,
    card_slot,
    illustrate_story,
    page_slot,
)

STEP_NAME = "image_safety"

# Bump when the instructions change: prompt text is a cache-key input by proxy.
PROMPT_VERSION = 1

# How many times one image may be redrawn after a failed verdict before the
# whole story is rejected. Each redraw is one image call plus one judge call.
IMAGE_SAFETY_MAX_REGENERATIONS = 2

IMAGE_SAFETY_TEMPERATURE = 0.0
IMAGE_SAFETY_MODEL_SETTINGS = ModelSettings(temperature=IMAGE_SAFETY_TEMPERATURE)

IMAGE_SAFETY_INSTRUCTIONS = """\
You are a strict safety judge for the illustrations of bedtime stories aimed
at pre-readers aged 3-6. Look at the one image you are given and return one
verdict per criterion — every criterion, exactly once, with a short reason:

- no_text: the image contains no text of any kind — no letters, words,
  numbers, signs, labels, or writing, legible or not.
- nothing_frightening: nothing a small child could find scary — no monsters,
  sharp teeth, menacing shapes or shadows, darkness as a threat, injury,
  danger, or distressed faces.
- calm: the image feels calm and gentle, fit for bedtime — soft, warm, and
  quiet rather than busy, loud, or tense.

Judge only what is in the image. Do not extend goodwill: a criterion passes
only when the image clearly satisfies it.
"""

JUDGE_PROMPT = "Judge this bedtime-story illustration against the three criteria."


class ImageSafetyRejectedError(Exception):
    """Raised when an image still fails after IMAGE_SAFETY_MAX_REGENERATIONS redraws."""

    def __init__(self, story: Story, failures: Sequence[str]) -> None:
        self.story = story
        self.failures = list(failures)
        super().__init__(
            f"story {story.id} rejected: image safety still failing after "
            f"{IMAGE_SAFETY_MAX_REGENERATIONS} regenerations: " + "; ".join(self.failures)
        )


def build_image_safety_agent(model: Model) -> Agent[None, ImageSafetyReport]:
    return Agent(
        model=model,
        output_type=ImageSafetyReport,
        instructions=IMAGE_SAFETY_INSTRUCTIONS,
        model_settings=IMAGE_SAFETY_MODEL_SETTINGS,
    )


def judge_image(
    png: bytes,
    settings: Settings,
    cache: ArtifactCache,
    *,
    model: Model | None = None,
) -> ImageSafetyReport:
    """Verdict one image on the three criteria; unchanged image, zero calls."""
    inputs = {
        "image_sha256": hashlib.sha256(png).hexdigest(),
        "model": settings.image_safety_model,
        "temperature": IMAGE_SAFETY_TEMPERATURE,
        "prompt_version": PROMPT_VERSION,
    }

    def produce() -> bytes:
        llm = model if model is not None else build_model(settings.image_safety_model, settings)
        # BinaryContent reaches OpenRouter as a data:image/png;base64 URL.
        image = BinaryContent(data=png, media_type="image/png")
        report = build_image_safety_agent(llm).run_sync([JUDGE_PROMPT, image]).output
        return report.model_dump_json().encode()

    return ImageSafetyReport.model_validate_json(run_step(cache, STEP_NAME, inputs, produce))


def _shown_images(illustrations: IllustrationSet) -> dict[str, Path]:
    """Every image a child sees, by slot. The character sheet never ships."""
    images = {page_slot(pid): path for pid, path in illustrations.page_images.items()}
    images.update({card_slot(key): path for key, path in illustrations.card_images.items()})
    images[COVER_SLOT] = illustrations.cover
    return images


def _failure_line(slot: str, report: ImageSafetyReport) -> str:
    reasons = ", ".join(f"{v.criterion}: {v.reason}" for v in report.verdicts if not v.passed)
    return f"{slot}: {reasons}"


def illustrate_safely(
    story: Story,
    settings: Settings,
    cache: ArtifactCache,
    *,
    transport: httpx.BaseTransport | None = None,
    judge_model: Model | None = None,
) -> IllustrationSet:
    """illustrate → judge every shown image → redraw failures, bounded.

    Each round re-runs illustrate with the failing slots' redraw counts bumped:
    passing images are cache hits, only rejected ones are redrawn. Judging is
    sequential — one Pydantic AI run per image — to keep the async model client
    on a single event loop.
    """
    llm = judge_model
    if llm is None:
        llm = build_model(settings.image_safety_model, settings)
    regenerations: dict[str, int] = {}
    while True:
        illustrations = illustrate_story(
            story, settings, cache, transport=transport, regenerations=regenerations
        )
        failed = {
            slot: report
            for slot, path in _shown_images(illustrations).items()
            if not (report := judge_image(path.read_bytes(), settings, cache, model=llm)).passed
        }
        if not failed:
            return illustrations
        exhausted = [
            _failure_line(slot, report)
            for slot, report in failed.items()
            if regenerations.get(slot, 0) >= IMAGE_SAFETY_MAX_REGENERATIONS
        ]
        if exhausted:
            raise ImageSafetyRejectedError(story, exhausted)
        for slot in failed:
            regenerations[slot] = regenerations.get(slot, 0) + 1
