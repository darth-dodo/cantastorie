"""Behavior specs for the cover field in the published manifest (AI-443).

The published manifest entry uses story.cover (the hashed portrait cover filename
produced by assemble_story) when set, and falls back to pages[0].image when the
story was assembled before the portrait cover feature landed (story.cover is None).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.pipeline.models import Page, PageAudio, Story, Theme, WordTiming
from src.pipeline.publish import STAGED_PREFIX, publish_story, stage_story
from src.pipeline.steps.assemble import AssembledStory, assemble_story
from src.pipeline.steps.illustrate import IllustrationSet

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"

SENTENCE = "The water sings shh shh."
PAGE_TEXT = " ".join([SENTENCE] * 8)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        staging_dir=tmp_path / "staging",
        r2_endpoint_url="https://r2.example.test",
        r2_access_key_id="test-access-key",
        r2_secret_access_key="test-secret-key",
        r2_bucket=BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _base_story(story_id: str = "cover-story", theme: Theme = "the_sleepy_sea") -> Story:
    """Return a story with 10 pages, each with enough words to pass content rules."""
    pages = [
        Page(
            id=f"p{n}",
            text=f"The little boat sails on, page {n}. " + PAGE_TEXT,
            next_page=f"p{n + 1}" if n < 10 else None,
        )
        for n in range(1, 11)
    ]
    return Story(
        id=story_id,
        language="it",
        title="Il mare tranquillo",
        theme=theme,
        shape="linear",
        pages=pages,
    )


def _assembled_with_cover(tmp_path: Path, story_id: str = "cover-story") -> AssembledStory:
    """Assemble a story that has a dedicated portrait cover image."""
    art = tmp_path / f"art-{story_id}"
    art.mkdir(parents=True, exist_ok=True)
    story = _base_story(story_id)
    page_images: dict[str, Path] = {}
    pages_with_audio = []
    for page in story.pages:
        audio = art / f"{page.id}.mp3"
        audio.write_bytes(f"mp3:{story_id}:{page.id}".encode())
        image = art / f"{page.id}.png"
        image.write_bytes(f"png:{story_id}:{page.id}".encode())
        page_images[page.id] = image
        pages_with_audio.append(
            page.model_copy(
                update={
                    "audio": PageAudio(
                        file=str(audio),
                        timings=[WordTiming(word="the", start_s=0.0, end_s=0.1)],
                    )
                }
            )
        )
    (art / "sheet.png").write_bytes(b"png:sheet")
    (art / "cover.png").write_bytes(b"png:portrait-cover-unique")
    illustrations = IllustrationSet(
        character_sheet=art / "sheet.png",
        character_sheet_hash="sheethash",
        page_images=page_images,
        cover=art / "cover.png",
    )
    story = story.model_copy(update={"pages": pages_with_audio})
    return assemble_story(story, illustrations)


def _assembled_without_cover(tmp_path: Path, story_id: str = "legacy-story") -> AssembledStory:
    """Assemble a story and then clear story.cover to simulate a legacy story (cover=None)."""
    assembled = _assembled_with_cover(tmp_path, story_id)
    # Simulate a legacy assembled story that predates the portrait cover feature.
    story_no_cover = assembled.story.model_copy(update={"cover": None})
    return AssembledStory(story=story_no_cover, assets=assembled.assets)


def _stage_prompts(client: S3Client, language: str = "it") -> None:
    for name in ("shelf_greeting", "story_start", "end_prompt", "audio_retry", "offline"):
        client.put_object(
            Bucket=BUCKET,
            Key=f"{STAGED_PREFIX}/prompts/{language}/{name}.0123456789abcdef.mp3",
            Body=f"mp3:{name}".encode(),
            ContentType="audio/mpeg",
        )


def _manifest(client: S3Client, language: str = "it") -> dict[str, Any]:
    key = f"published/{language}/manifest.json"
    loaded: dict[str, Any] = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    return loaded


# --- Portrait cover in manifest -------------------------------------------


def test_manifest_uses_story_cover_when_set(tmp_path: Path, s3: S3Client) -> None:
    """Given a story assembled with a portrait cover (story.cover is set),
    When the story is published,
    Then the manifest entry's 'cover' URL points to story.cover (not pages[0].image).
    """
    settings = _settings(tmp_path)
    assembled = _assembled_with_cover(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3)

    manifest = _manifest(s3)
    entry = next(e for e in manifest["stories"] if e["id"] == assembled.story.id)
    assert assembled.story.cover is not None
    assert entry["cover"] == f"{PUBLIC_BASE}/stories/{assembled.story.id}/{assembled.story.cover}"
    # Confirm it does NOT point to the first page image
    assert assembled.story.pages[0].image not in entry["cover"]


def test_manifest_falls_back_to_first_page_when_cover_is_none(tmp_path: Path, s3: S3Client) -> None:
    """Given a legacy story where story.cover is None,
    When the story is published,
    Then the manifest entry's 'cover' URL falls back to pages[0].image.
    """
    settings = _settings(tmp_path)
    assembled = _assembled_without_cover(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3)

    manifest = _manifest(s3)
    entry = next(e for e in manifest["stories"] if e["id"] == assembled.story.id)
    assert assembled.story.cover is None
    first_page_image = assembled.story.pages[0].image
    assert entry["cover"] == f"{PUBLIC_BASE}/stories/{assembled.story.id}/{first_page_image}"


def test_cover_file_is_uploaded_to_published_bucket(tmp_path: Path, s3: S3Client) -> None:
    """Given a story with story.cover set,
    When the story is staged and published,
    Then the cover file exists in the published bucket at the expected key.
    """
    settings = _settings(tmp_path)
    assembled = _assembled_with_cover(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3)

    assert assembled.story.cover is not None
    cover_key = f"published/stories/{assembled.story.id}/{assembled.story.cover}"
    head = s3.head_object(Bucket=BUCKET, Key=cover_key)
    assert head["ContentLength"] > 0
