"""Behavior specs for the portrait cover emitted by the illustrate step (AI-443).

The illustrate step generates a dedicated portrait cover image per story. The
cover prompt explicitly frames the image for a portrait 5/6 tile (tall), and the
assembled story's ``story.cover`` field is set to the hashed cover filename so
publish can resolve it to a URL.
"""

import base64
import hashlib
import json
import re
from pathlib import Path

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.cache import ArtifactCache
from src.pipeline.models import Page, PageAudio, Story, WordTiming
from src.pipeline.steps.assemble import assemble_story
from src.pipeline.steps.illustrate import (
    IllustrationSet,
    illustrate_story,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        image_model="google/gemini-2.5-flash-image",
    )


SENTENCE = "The water sings shh shh."
PAGE_TEXT = " ".join([SENTENCE] * 8)


def _story() -> Story:
    pages = [
        Page(
            id=f"page-{n}",
            text=f"The little boat rocks gently on the quiet water, page {n}. " + PAGE_TEXT,
            next_page=f"page-{n + 1}" if n < 10 else None,
        )
        for n in range(1, 11)
    ]
    return Story(
        id="story-cover-test",
        language="it",
        title="La barchetta sonnolenta",
        theme="the_little_boat",
        shape="linear",
        pages=pages,
    )


class _FakeImageModel:
    """MockTransport that records every request, returns deterministic PNG bytes."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        prompt = body["messages"][0]["content"][0]["text"]
        fake_png = b"png:" + hashlib.sha256(prompt.encode()).digest()
        data_url = "data:image/png;base64," + base64.b64encode(fake_png).decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "images": [{"image_url": {"url": data_url}}]}}
                ]
            },
        )


def _narrated_illustrations(tmp_path: Path, story: Story) -> tuple[Story, IllustrationSet]:
    """Produce a fully narrated + illustrated story suitable for assemble_story."""
    audio_dir = tmp_path / "narrate"
    audio_dir.mkdir(parents=True, exist_ok=True)
    image_dir = tmp_path / "illustrate"
    image_dir.mkdir(parents=True, exist_ok=True)

    pages_with_audio: list[Page] = []
    page_images: dict[str, Path] = {}
    for page in story.pages:
        audio_path = audio_dir / f"{page.id}.wav"
        audio_path.write_bytes(f"mp3:{page.id}".encode())
        image_path = image_dir / f"{page.id}.png"
        image_path.write_bytes(f"png:{page.id}".encode())
        page_images[page.id] = image_path
        pages_with_audio.append(
            page.model_copy(
                update={
                    "audio": PageAudio(
                        file=str(audio_path),
                        timings=[WordTiming(word="the", start_s=0.0, end_s=0.1)],
                    )
                }
            )
        )

    sheet = image_dir / "sheet.png"
    sheet.write_bytes(b"png:sheet")
    cover_file = image_dir / "cover.png"
    cover_file.write_bytes(b"png:portrait-cover")

    illustrations = IllustrationSet(
        character_sheet=sheet,
        character_sheet_hash="sheethash",
        page_images=page_images,
        cover=cover_file,
    )
    narrated_story = story.model_copy(update={"pages": pages_with_audio})
    return narrated_story, illustrations


# --- Portrait cover prompt -----------------------------------------------


def test_the_cover_prompt_frames_for_portrait_orientation(tmp_path: Path) -> None:
    """Given a story to illustrate,
    When the illustration step runs,
    Then the cover request's prompt includes portrait orientation / tall framing —
    confirming the cover is sized for a 5/6-tile phone display.
    """
    model = _FakeImageModel()
    cache = ArtifactCache(tmp_path / "story-cover-test")

    illustrate_story(_story(), _settings(), cache, transport=model.transport())

    # The last request before the first page is the cover (sheet is first).
    # Find the cover request: it is the one that mentions the title.
    cover_requests = [
        r
        for r in model.requests
        if "La barchetta sonnolenta" in r["messages"][0]["content"][0]["text"]  # type: ignore[index]
    ]
    assert cover_requests, "no cover request found (expected a prompt mentioning the story title)"
    cover_prompt = cover_requests[0]["messages"][0]["content"][0]["text"]  # type: ignore[index]
    assert isinstance(cover_prompt, str)
    assert "portrait" in cover_prompt.lower() or "tall" in cover_prompt.lower()


# --- assemble_story sets story.cover ------------------------------------


def test_assemble_sets_story_cover_to_a_hashed_filename(tmp_path: Path) -> None:
    """Given a story with a portrait cover in its IllustrationSet,
    When the story is assembled,
    Then story.cover is set to a content-hashed filename (cover.{hash8}.webp),
    and the cover is registered in AssembledStory.assets.
    """
    story = _story()
    narrated, illustrations = _narrated_illustrations(tmp_path, story)

    assembled = assemble_story(narrated, illustrations)

    assert assembled.story.cover is not None
    assert re.fullmatch(r"cover\.[0-9a-f]{8}\.webp", assembled.story.cover)
    assert assembled.story.cover in assembled.assets
    assert assembled.assets[assembled.story.cover].exists()


def test_assemble_cover_name_is_derived_from_cover_bytes(tmp_path: Path) -> None:
    """Given two stories whose cover images differ by one byte,
    When both are assembled,
    Then their cover filenames carry different hashes — the name is content-addressed.
    """
    story = _story()

    # First story — cover bytes b"png:portrait-cover"
    narrated1, illustrations1 = _narrated_illustrations(tmp_path / "a", story)
    assembled1 = assemble_story(narrated1, illustrations1)

    # Second story — different cover bytes
    narrated2, illustrations2 = _narrated_illustrations(tmp_path / "b", story)
    illustrations2.cover.write_bytes(b"png:different-cover")
    assembled2 = assemble_story(narrated2, illustrations2)

    assert assembled1.story.cover != assembled2.story.cover
