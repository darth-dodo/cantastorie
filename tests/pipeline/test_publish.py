"""Behavior specs for stage and publish (AI-361).

**stage** lays an assembled story out locally for review; **publish** is the
only writer to published/ (docs/architecture.md "Privacy Architecture"), and it
is idempotent — content-hashed names mean a repeat publish uploads nothing. All
S3 traffic is served by moto's in-memory bucket: zero network.
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
from src.pipeline.content_rules import check_story
from src.pipeline.models import Page, PageAudio, Story, Theme, WordTiming
from src.pipeline.publish import publish_story, stage_story
from src.pipeline.steps.assemble import AssembledStory, assemble_story
from src.pipeline.steps.illustrate import IllustrationSet

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"

SENTENCE = "The water sings shh shh."
PAGE_TEXT = " ".join([SENTENCE] * 8)


@pytest.fixture
def s3() -> Iterator[S3Client]:
    """A moto-backed S3 client with the published bucket already created."""
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
    """A validated, assembled story with its source artifacts on disk."""
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


def _stage_prompts(settings: Settings, language: str = "it") -> None:
    """Stage the slice-1 spoken prompts under staging/prompts/{lang}/ the way
    synthesize_utterances would: {name}.{contenthash}.mp3."""
    prompt_dir = settings.staging_dir / "prompts" / language
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for name in ("shelf_greeting", "story_start", "end_prompt"):
        (prompt_dir / f"{name}.0123456789abcdef.mp3").write_bytes(f"mp3:{name}".encode())


def _keys(client: S3Client, prefix: str = "") -> list[str]:
    response = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]


def _manifest(client: S3Client, language: str = "it") -> dict[str, Any]:
    key = f"published/{language}/manifest.json"
    loaded: dict[str, Any] = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    return loaded


# --- stage: local only, no bucket reachable ---------------------------------


def test_stage_lays_story_json_and_every_asset_out_locally(tmp_path: Path, s3: S3Client) -> None:
    """Given an assembled story,
    When it is staged,
    Then staging/{story-id}/ holds story.json beside every hashed audio and
    image — text, audio, images together for the operator to open.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)

    story_dir = stage_story(assembled, settings)

    assert story_dir == settings.staging_dir / assembled.story.id
    assert (story_dir / "story.json").exists()
    staged = {path.name for path in story_dir.iterdir()}
    assert staged == {"story.json", *assembled.assets.keys()}
    assert len(assembled.assets) == 20  # 10 pages, each an audio and an image


def test_staging_reaches_no_bucket_only_publish_writes_to_published(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given staging runs (docs/architecture.md "Privacy Architecture": only
    the publish step writes to published/),
    When only stage has run,
    Then the bucket holds nothing — nothing reaches published/ except via publish.
    """
    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path), settings)
    _stage_prompts(settings)

    assert _keys(s3, prefix="published/") == []


# --- publish: uploads, manifest, idempotency --------------------------------


def test_publish_uploads_the_story_its_assets_and_writes_the_manifest(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a staged story and its language prompts,
    When it is published,
    Then story.json and every hashed asset land under
    published/stories/{id}/, and published/it/manifest.json lists the story
    with its player-shaped fields and prompt URLs.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings)
    _stage_prompts(settings)

    result = publish_story(assembled.story.id, settings, client=s3)

    story_id = assembled.story.id
    keys = set(_keys(s3))
    assert f"published/stories/{story_id}/story.json" in keys
    assert f"published/stories/{story_id}/{next(iter(assembled.assets))}" in keys
    assert "published/it/manifest.json" in keys
    # Prompts are first-class published assets under published/prompts/it/.
    assert "published/prompts/it/shelf_greeting.0123456789abcdef.mp3" in keys
    assert result.manifest_story_ids == [story_id]

    manifest = _manifest(s3)
    assert manifest["language"] == "it"
    entry = next(s for s in manifest["stories"] if s["id"] == story_id)
    assert entry["title"] == "La barchetta"
    assert entry["story"] == f"{PUBLIC_BASE}/stories/{story_id}/story.json"
    assert entry["wash"] == "wash-barchetta"
    assert set(entry) == {"id", "title", "wash", "story"}
    assert manifest["prompts"] == {
        "greeting": f"{PUBLIC_BASE}/prompts/it/shelf_greeting.0123456789abcdef.mp3",
        "story_start": f"{PUBLIC_BASE}/prompts/it/story_start.0123456789abcdef.mp3",
        "end": f"{PUBLIC_BASE}/prompts/it/end_prompt.0123456789abcdef.mp3",
    }


def test_published_manifest_wash_is_theme_mapped_not_story_id(tmp_path: Path, s3: S3Client) -> None:
    """Given stories with known themes,
    When they are published,
    Then each manifest entry's wash is the CSS class mapped from the story's
    theme — not the synthetic wash-{story-id} that produces no styling.
    """
    settings = _settings(tmp_path)

    sea = _assembled(tmp_path, story_id="sea-story", theme="the_sleepy_sea")
    bakery = _assembled(tmp_path, story_id="bakery-story", theme="bakery_morning")
    stage_story(sea, settings)
    stage_story(bakery, settings)
    _stage_prompts(settings)

    publish_story("sea-story", settings, client=s3)
    publish_story("bakery-story", settings, client=s3)

    manifest = _manifest(s3)
    washes = {entry["id"]: entry["wash"] for entry in manifest["stories"]}
    assert washes["sea-story"] == "wash-barchetta"
    assert washes["bakery-story"] == "wash-panetteria"
    assert washes["sea-story"] != "wash-sea-story"


def test_republishing_an_unchanged_story_uploads_nothing(tmp_path: Path, s3: S3Client) -> None:
    """Given a story already published,
    When it is published again unchanged,
    Then not one object is re-uploaded — the content-hashed names and the
    HEAD-before-PUT skip make publish idempotent (manifest included).
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings)
    _stage_prompts(settings)

    first = publish_story(assembled.story.id, settings, client=s3)
    second = publish_story(assembled.story.id, settings, client=s3)

    assert first.uploaded  # the first publish did real work
    assert second.uploaded == []  # the second uploaded nothing at all
    assert set(second.skipped) == set(first.uploaded)


def test_publishing_a_second_story_appends_to_the_manifest(tmp_path: Path, s3: S3Client) -> None:
    """Given one story already published,
    When a second, different story is published,
    Then the manifest lists both — a second story appends, never clobbers.
    """
    settings = _settings(tmp_path)
    first = _assembled(tmp_path, story_id="story-one", title="Prima")
    second = _assembled(tmp_path, story_id="story-two", title="Seconda")
    stage_story(first, settings)
    stage_story(second, settings)
    _stage_prompts(settings)

    publish_story("story-one", settings, client=s3)
    result = publish_story("story-two", settings, client=s3)

    assert sorted(result.manifest_story_ids) == ["story-one", "story-two"]
    titles = {entry["id"]: entry["title"] for entry in _manifest(s3)["stories"]}
    assert titles == {"story-one": "Prima", "story-two": "Seconda"}


def test_republishing_the_same_id_replaces_its_entry_rather_than_duplicating(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a story published once,
    When the same id is re-staged with a new title and published,
    Then the manifest carries exactly one entry for that id, now updated.
    """
    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path, story_id="story-one", title="Prima"), settings)
    publish_story("story-one", settings, client=s3)

    stage_story(_assembled(tmp_path, story_id="story-one", title="Prima riveduta"), settings)
    result = publish_story("story-one", settings, client=s3)

    assert result.manifest_story_ids == ["story-one"]
    assert [entry["title"] for entry in _manifest(s3)["stories"]] == ["Prima riveduta"]


def test_the_published_story_json_conforms_to_the_content_rules(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a published story (docs/architecture.md "Testing": the content
    rules are enforced as pytest assertions against every published story.json),
    When story.json is fetched back from the bucket,
    Then it parses as a Story and breaks no content rule.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings)
    publish_story(assembled.story.id, settings, client=s3)

    key = f"published/stories/{assembled.story.id}/story.json"
    published = Story.model_validate(
        json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    )
    assert check_story(published) == []
