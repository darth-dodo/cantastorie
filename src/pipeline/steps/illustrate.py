"""Illustration step: character sheet first, then pages and cover from it.

One character/style reference sheet is generated per story, and that same
sheet is fed as an image input to every page and cover generation. Pages are
never chained page-to-page — chaining compounds drift, the sheet keeps every
board consistent (docs/architecture.md → "Model roles").

Transport note: pydantic-ai 2.5.0 supports image *inputs* everywhere but has
no support for image *generation* outputs over OpenRouter chat completions
(no ``modalities`` request field, ``message.images`` never parsed), so this
step calls OpenRouter's chat completions endpoint directly with httpx.
OpenRouter stays the only gateway; the ban is on direct vendor SDKs.
"""

import base64
import functools
import hashlib
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from src.config import Settings
from src.observability import typed_traceable
from src.pipeline.cache import ArtifactCache, cache_key, run_step
from src.pipeline.models import Story

# The locked style (docs/product.md → decision log "Style", **Watercolor
# boards**, **Calm pictures**). A module-level constant so it is diffable and
# participates verbatim in every cache key — edit it and every image is
# knowingly regenerated.
STYLE_PROMPT = (
    "Soft watercolor illustration for a children's bedtime story. "
    "Warm palette, gentle diffuse light, rounded friendly characters with "
    "soft edges — bedtime, not Saturday cartoons. Nothing frightening: "
    "no darkness, no sharp teeth, no menacing shapes or shadows. "
    "The image must contain no text: no letters, no words, no numbers, "
    "no signs, no writing of any kind."
)

STEP_NAME = "illustrate"

# Images are stored as raw PNG bytes; store and load MUST use this same
# suffix or every lookup misses and every re-run re-buys the image.
IMAGE_SUFFIX = ".png"


class IllustrationSet(BaseModel):
    """Paths of the persisted illustration artifacts for one story."""

    character_sheet: Path
    character_sheet_hash: str
    page_images: dict[str, Path]  # keyed by page id
    cover: Path


class ImageClient:
    """OpenRouter image generation over chat completions (direct httpx)."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._model = settings.image_model
        # The key is unwrapped only here, at the transport boundary — it
        # lives in a header, never in this object's attributes or repr.
        self._client = httpx.Client(
            base_url=settings.openrouter_base_url,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"},
            transport=transport,
            timeout=300.0,
        )

    def __repr__(self) -> str:
        return f"ImageClient(model={self._model!r})"

    def close(self) -> None:
        self._client.close()

    @typed_traceable(name="illustrate.generate")
    def generate(self, prompt: str, reference_png: bytes | None = None) -> bytes:
        """Generate one image; the optional reference image rides along as input."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if reference_png is not None:
            data_uri = "data:image/png;base64," + base64.b64encode(reference_png).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": data_uri}})
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"],
            },
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        images = message.get("images") or []
        if not images:
            raise RuntimeError(f"model {self._model!r} returned no image for the prompt")
        data_url: str = images[0]["image_url"]["url"]
        _, _, encoded = data_url.partition("base64,")
        return base64.b64decode(encoded)


def _story_summary(story: Story) -> str:
    """The sheet's notion of the story: title and theme only.

    Deliberately excludes page texts — the sheet must survive a page edit,
    otherwise editing page 5 would rehash the sheet and regenerate every page.
    """
    return f"{story.title} — theme: {story.theme.replace('_', ' ')}"


def _sheet_prompt(summary: str) -> str:
    return (
        f"{STYLE_PROMPT} Create a character and style reference sheet for the "
        f"bedtime story: {summary}. Show each main character full-body on a "
        "plain warm background, in the exact style every page will follow."
    )


def _page_prompt(page_text: str) -> str:
    return (
        f"{STYLE_PROMPT} Using the attached character reference sheet, keep "
        "every character exactly as drawn there and illustrate this story "
        f"page: {page_text}"
    )


def _cover_prompt(title: str) -> str:
    return (
        f"{STYLE_PROMPT} Using the attached character reference sheet, keep "
        "every character exactly as drawn there and paint a warm, inviting "
        f"cover illustration for the bedtime story titled: {title}. "
        "The title itself must not appear — no text at all."
    )


def _artifact_path(cache: ArtifactCache, inputs: dict[str, str]) -> Path:
    return cache.story_dir / STEP_NAME / f"{cache_key(inputs)}{IMAGE_SUFFIX}"


def _generate_page(client: ImageClient, page_text: str, sheet: bytes) -> bytes:
    return client.generate(_page_prompt(page_text), reference_png=sheet)


def illustrate_story(
    story: Story,
    settings: Settings,
    cache: ArtifactCache,
    transport: httpx.BaseTransport | None = None,
) -> IllustrationSet:
    """Produce the character sheet, one image per page, and the cover.

    Cache keys follow docs/architecture.md → "Content-addressed caching":
    the sheet is keyed on story summary + style prompt + model; every page
    (and the cover) is keyed on its text + the sheet's content hash + style
    prompt + model. Editing one page regenerates exactly that page.
    """
    client = ImageClient(settings, transport=transport)
    try:
        # 1. The sheet comes first — every other image derives from it.
        summary = _story_summary(story)
        sheet_inputs = {
            "story_summary": summary,
            "style_prompt": STYLE_PROMPT,
            "model": settings.image_model,
        }
        sheet = run_step(
            cache,
            STEP_NAME,
            sheet_inputs,
            lambda: client.generate(_sheet_prompt(summary)),
            suffix=IMAGE_SUFFIX,
        )
        sheet_hash = hashlib.sha256(sheet).hexdigest()

        # 2. Every page gets the same sheet as reference — never the
        #    previous page's image.
        page_images: dict[str, Path] = {}
        for page in story.pages:
            page_inputs = {
                "page_text": page.text,
                "character_sheet_hash": sheet_hash,
                "style_prompt": STYLE_PROMPT,
                "model": settings.image_model,
            }
            run_step(
                cache,
                STEP_NAME,
                page_inputs,
                functools.partial(_generate_page, client, page.text, sheet),
                suffix=IMAGE_SUFFIX,
            )
            page_images[page.id] = _artifact_path(cache, page_inputs)

        # 3. The cover derives from the same sheet.
        cover_inputs = {
            "cover_title": story.title,
            "character_sheet_hash": sheet_hash,
            "style_prompt": STYLE_PROMPT,
            "model": settings.image_model,
        }
        run_step(
            cache,
            STEP_NAME,
            cover_inputs,
            lambda: client.generate(_cover_prompt(story.title), reference_png=sheet),
            suffix=IMAGE_SUFFIX,
        )
    finally:
        client.close()

    return IllustrationSet(
        character_sheet=_artifact_path(cache, sheet_inputs),
        character_sheet_hash=sheet_hash,
        page_images=page_images,
        cover=_artifact_path(cache, cover_inputs),
    )
