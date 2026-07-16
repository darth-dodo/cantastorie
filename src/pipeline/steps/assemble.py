"""assemble (AI-361): the final story.json, built from step outputs and validated.

The narrate and illustrate steps each leave content-addressed artifacts in the
story's cache folder; assemble braids them into the one document the player
plays — page texts, structure, and asset references — and refuses to hand on
anything that would fail at bedtime.

Three checks are HARD errors, not warnings (docs/architecture.md "Testing": the
content rules are enforced as pipeline validation in assemble):

- the content rules (reused verbatim from content_rules.py) — a violation
  raises with the typed ``ContentViolation`` list, precise enough to have
  driven a revise;
- every referenced asset resolves to real bytes on disk;
- every page carries audio (word timings are empty until slice 6's Deepgram
  STT pass reconstructs them — ADR-004).

Published asset names embed a content hash and so are immutable, cache-forever
(docs/architecture.md "R2 layout"): ``p1.{hash8}.wav`` for audio, ``p1.{hash8}
.webp`` for images. The rewritten references are relative filenames — the
player resolves them against the story.json URL — so the assembled Story is
exactly the shape the dev fixture already plays.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from src.pipeline.content_rules import ContentViolation, check_story
from src.pipeline.models import Page, PageAudio, Story
from src.pipeline.steps.illustrate import IllustrationSet

# Enough of the SHA-256 to make collisions a non-worry while keeping filenames
# short (docs/architecture.md "R2 layout": p1.{hash}.wav, matching the fixture).
CONTENT_HASH_LENGTH = 8

AUDIO_SUFFIX = ".wav"
IMAGE_SUFFIX = ".webp"


class ContentRulesViolation(Exception):
    """Assembly refused: the story breaks one or more content limits.

    Carries the typed ``ContentViolation`` list so a caller sees exactly which
    rule broke where — the same values the revise loop acts on.
    """

    def __init__(self, violations: Sequence[ContentViolation]) -> None:
        self.violations = list(violations)
        super().__init__(
            "story breaks content rules: "
            + "; ".join(f"{v.rule}: {v.detail}" for v in self.violations)
        )


class MissingAssetError(Exception):
    """A page references an asset that is not on disk — assembly cannot proceed."""

    def __init__(self, page_id: str, kind: str, path: Path | None) -> None:
        self.page_id = page_id
        self.kind = kind
        self.path = path
        where = f": {path}" if path is not None else ""
        super().__init__(f"page {page_id} is missing its {kind} asset{where}")


class AssembledStory(BaseModel):
    """The validated story.json plus the assets it now references by hashed name.

    ``assets`` maps each published filename (``p1.{hash8}.wav``, ``p1.{hash8}
    .webp``) to the source bytes on disk, so stage and publish copy without
    re-deriving anything.
    """

    story: Story
    assets: dict[str, Path]


def _hashed_name(page_id: str, data: bytes, suffix: str) -> str:
    digest = hashlib.sha256(data).hexdigest()[:CONTENT_HASH_LENGTH]
    return f"{page_id}.{digest}{suffix}"


def assemble_story(story: Story, illustrations: IllustrationSet) -> AssembledStory:
    """Braid narration and illustration into the final, validated story.json.

    Content rules are checked first (a violation raises before any asset work),
    then every page must resolve its audio, its timings, and its image. The
    returned Story has each reference rewritten to its immutable hashed name.
    """
    violations = check_story(story)
    if violations:
        raise ContentRulesViolation(violations)

    assets: dict[str, Path] = {}
    assembled_pages: list[Page] = []
    for page in story.pages:
        if page.audio is None:
            raise MissingAssetError(page.id, "audio", None)
        audio_src = Path(page.audio.file)
        if not audio_src.exists():
            raise MissingAssetError(page.id, "audio", audio_src)

        image_src = illustrations.page_images.get(page.id)
        if image_src is None or not image_src.exists():
            raise MissingAssetError(page.id, "image", image_src)

        audio_bytes = audio_src.read_bytes()
        image_bytes = image_src.read_bytes()
        audio_name = _hashed_name(page.id, audio_bytes, AUDIO_SUFFIX)
        image_name = _hashed_name(page.id, image_bytes, IMAGE_SUFFIX)
        assets[audio_name] = audio_src
        assets[image_name] = image_src

        assembled_pages.append(
            page.model_copy(
                update={
                    "audio": PageAudio(file=audio_name, timings=page.audio.timings),
                    "image": image_name,
                }
            )
        )

    return AssembledStory(story=story.model_copy(update={"pages": assembled_pages}), assets=assets)
