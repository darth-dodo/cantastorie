"""Behavior specs for the family overlay publish lane (private stories).

Operators publish to the shared shelf (``published/{lang}/manifest.json``);
families publish to a private overlay (``published/families/{token}/{lang}/
manifest.json``) that reaches only their own child. The two lanes never cross,
and there is no promotion of private → global.

All S3 traffic is served by moto's in-memory bucket: zero network. Mirrors the
fixtures in tests/pipeline/test_publish.py.
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
from src.pipeline.publish import STAGED_PREFIX, publish_story, stage_story, unpublish_story
from src.pipeline.steps.assemble import AssembledStory, assemble_story
from src.pipeline.steps.illustrate import IllustrationSet

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"

FAMILY = "a" * 32
OTHER_FAMILY = "b" * 32

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
        r2_bucket=BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _assembled(
    tmp_path: Path,
    *,
    story_id: str = "the-sleepy-sea-it-abc12345",
    title: str = "La barchetta",
    theme: Theme = "the_sleepy_sea",
) -> AssembledStory:
    art = tmp_path / f"art-{story_id}"
    art.mkdir(parents=True, exist_ok=True)
    pages: list[Page] = []
    page_images: dict[str, Path] = {}
    for n in range(1, 11):
        pid = f"p{n}"
        audio = art / f"{pid}.mp3"
        audio.write_bytes(f"mp3:{story_id}:{pid}".encode())
        image = art / f"{pid}.png"
        image.write_bytes(f"png:{story_id}:{pid}".encode())
        page_images[pid] = image
        pages.append(
            Page(
                id=pid,
                text=PAGE_TEXT,
                audio=PageAudio(
                    file=str(audio), timings=[WordTiming(word="the", start_s=0.0, end_s=0.1)]
                ),
                next_page=f"p{n + 1}" if n < 10 else None,
            )
        )
    (art / "sheet.png").write_bytes(b"png:sheet")
    (art / "cover.png").write_bytes(b"png:cover")
    illustrations = IllustrationSet(
        character_sheet=art / "sheet.png",
        character_sheet_hash="sheethash",
        page_images=page_images,
        cover=art / "cover.png",
    )
    story = Story(
        id=story_id,
        language="it",
        title=title,
        theme=theme,
        shape="linear",
        pages=pages,
    )
    return assemble_story(story, illustrations)


def _stage_prompts(client: S3Client, language: str = "it") -> None:
    for name in ("shelf_greeting", "story_start", "end_prompt", "audio_retry", "offline"):
        client.put_object(
            Bucket=BUCKET,
            Key=f"{STAGED_PREFIX}/prompts/{language}/{name}.0123456789abcdef.mp3",
            Body=f"mp3:{name}".encode(),
            ContentType="audio/mpeg",
        )


def _keys(client: S3Client, prefix: str = "") -> list[str]:
    response = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]


def _manifest(client: S3Client, key: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    return loaded


def test_overlay_publish_writes_under_the_family_prefix(tmp_path: Path, s3: S3Client) -> None:
    """A family publish lands under published/families/{token}/… and its own manifest."""
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    result = publish_story(assembled.story.id, settings, client=s3, family_token=FAMILY)

    story_id = assembled.story.id
    keys = set(_keys(s3))
    assert f"published/families/{FAMILY}/stories/{story_id}/story.json" in keys
    assert f"published/families/{FAMILY}/it/manifest.json" in keys
    assert f"published/families/{FAMILY}/prompts/it/shelf_greeting.0123456789abcdef.mp3" in keys
    assert result.manifest_story_ids == [story_id]

    manifest = _manifest(s3, f"published/families/{FAMILY}/it/manifest.json")
    entry = next(s for s in manifest["stories"] if s["id"] == story_id)
    assert entry["story"] == (f"{PUBLIC_BASE}/families/{FAMILY}/stories/{story_id}/story.json")
    assert entry["cover"].startswith(f"{PUBLIC_BASE}/families/{FAMILY}/stories/{story_id}/")


def test_overlay_publish_never_touches_the_shared_manifest(tmp_path: Path, s3: S3Client) -> None:
    """The shared shelf stays empty when only a family publishes."""
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3, family_token=FAMILY)

    assert _keys(s3, prefix="published/it/manifest.json") == []
    assert _keys(s3, prefix="published/stories/") == []


def test_two_families_stay_isolated(tmp_path: Path, s3: S3Client) -> None:
    """Family A's overlay never references family B's assets, and vice versa."""
    settings = _settings(tmp_path)
    a = _assembled(tmp_path, story_id="story-a", title="Storia A")
    b = _assembled(tmp_path, story_id="story-b", title="Storia B")
    stage_story(a, settings, client=s3)
    stage_story(b, settings, client=s3)
    _stage_prompts(s3)

    publish_story("story-a", settings, client=s3, family_token=FAMILY)
    publish_story("story-b", settings, client=s3, family_token=OTHER_FAMILY)

    man_a = _manifest(s3, f"published/families/{FAMILY}/it/manifest.json")
    man_b = _manifest(s3, f"published/families/{OTHER_FAMILY}/it/manifest.json")
    assert [e["id"] for e in man_a["stories"]] == ["story-a"]
    assert [e["id"] for e in man_b["stories"]] == ["story-b"]
    assert OTHER_FAMILY not in json.dumps(man_a)
    assert FAMILY not in json.dumps(man_b)


def test_overlay_publish_rejects_a_garbage_family_token(tmp_path: Path, s3: S3Client) -> None:
    """A non-canonical token must never become an R2 prefix."""
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)

    for bad in ("", "not-hex", "a" * 31, "A" * 32, "../evil", "a" * 33):
        with pytest.raises(ValueError, match="family token"):
            publish_story(assembled.story.id, settings, client=s3, family_token=bad)


def test_unpublish_removes_a_family_overlay_story(tmp_path: Path, s3: S3Client) -> None:
    """Deleting an overlay story clears its manifest entry and its assets."""
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)
    story_id = assembled.story.id
    publish_story(story_id, settings, client=s3, family_token=FAMILY)

    unpublish_story(story_id, settings, client=s3, family_token=FAMILY)

    assert _keys(s3, prefix=f"published/families/{FAMILY}/stories/{story_id}/") == []
    manifest = _manifest(s3, f"published/families/{FAMILY}/it/manifest.json")
    assert manifest["stories"] == []


def test_shared_publish_is_unchanged_by_the_overlay_parameter(tmp_path: Path, s3: S3Client) -> None:
    """Calling publish with family_token=None is byte-identical to the shared path."""
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3, family_token=None)

    story_id = assembled.story.id
    keys = set(_keys(s3))
    assert f"published/stories/{story_id}/story.json" in keys
    assert "published/it/manifest.json" in keys
    assert not any(k.startswith("published/families/") for k in keys)
