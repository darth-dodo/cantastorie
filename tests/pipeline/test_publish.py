"""Behavior specs for stage and publish (AI-361).

**stage** uploads an assembled story to R2 under pending/staged/{story-id}/
for review; **publish** is the only writer to published/ (docs/architecture.md
"Privacy Architecture"), and it is idempotent — content-hashed names mean a
repeat publish uploads nothing. All S3 traffic is served by moto's in-memory
bucket: zero network.
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
from src.pipeline.publish import STAGED_PREFIX, publish_story, stage_story, unpublish_story
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
    for name in ("shelf_greeting", "story_start", "end_prompt"):
        client.put_object(
            Bucket=BUCKET,
            Key=f"{STAGED_PREFIX}/prompts/{language}/{name}.0123456789abcdef.mp3",
            Body=f"mp3:{name}".encode(),
            ContentType="audio/mpeg",
        )


def _keys(client: S3Client, prefix: str = "") -> list[str]:
    response = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]


def _manifest(client: S3Client, language: str = "it") -> dict[str, Any]:
    key = f"published/{language}/manifest.json"
    loaded: dict[str, Any] = json.loads(client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    return loaded


def test_stage_uploads_story_json_and_every_asset_to_r2(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)

    prefix = stage_story(assembled, settings, client=s3)

    assert prefix == f"{STAGED_PREFIX}/{assembled.story.id}"
    staged_keys = _keys(s3, prefix=f"{STAGED_PREFIX}/{assembled.story.id}/")
    staged_names = {k.rsplit("/", 1)[-1] for k in staged_keys}
    assert "story.json" in staged_names
    assert staged_names == {"story.json", *assembled.assets.keys()}
    assert len(assembled.assets) == 20


def test_staging_writes_only_to_pending_not_published(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path), settings, client=s3)
    _stage_prompts(s3)

    assert _keys(s3, prefix="published/") == []


def test_publish_uploads_the_story_its_assets_and_writes_the_manifest(
    tmp_path: Path, s3: S3Client
) -> None:
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    result = publish_story(assembled.story.id, settings, client=s3)

    story_id = assembled.story.id
    keys = set(_keys(s3))
    assert f"published/stories/{story_id}/story.json" in keys
    assert f"published/stories/{story_id}/{next(iter(assembled.assets))}" in keys
    assert "published/it/manifest.json" in keys
    assert "published/prompts/it/shelf_greeting.0123456789abcdef.mp3" in keys
    assert result.manifest_story_ids == [story_id]

    manifest = _manifest(s3)
    assert manifest["language"] == "it"
    entry = next(s for s in manifest["stories"] if s["id"] == story_id)
    assert entry["title"] == "La barchetta"
    assert entry["story"] == f"{PUBLIC_BASE}/stories/{story_id}/story.json"
    assert entry["wash"] == "wash-barchetta"
    assert set(entry) == {"id", "title", "wash", "story", "cover"}
    assert manifest["prompts"] == {
        "greeting": f"{PUBLIC_BASE}/prompts/it/shelf_greeting.0123456789abcdef.mp3",
        "story_start": f"{PUBLIC_BASE}/prompts/it/story_start.0123456789abcdef.mp3",
        "end": f"{PUBLIC_BASE}/prompts/it/end_prompt.0123456789abcdef.mp3",
    }


def test_published_manifest_wash_is_theme_mapped_not_story_id(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)

    sea = _assembled(tmp_path, story_id="sea-story", theme="the_sleepy_sea")
    bakery = _assembled(tmp_path, story_id="bakery-story", theme="bakery_morning")
    stage_story(sea, settings, client=s3)
    stage_story(bakery, settings, client=s3)
    _stage_prompts(s3)

    publish_story("sea-story", settings, client=s3)
    publish_story("bakery-story", settings, client=s3)

    manifest = _manifest(s3)
    washes = {entry["id"]: entry["wash"] for entry in manifest["stories"]}
    assert washes["sea-story"] == "wash-barchetta"
    assert washes["bakery-story"] == "wash-panetteria"
    assert washes["sea-story"] != "wash-sea-story"


def test_republishing_an_unchanged_story_uploads_nothing(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    first = publish_story(assembled.story.id, settings, client=s3)
    second = publish_story(assembled.story.id, settings, client=s3)

    assert first.uploaded
    assert second.uploaded == []
    assert set(second.skipped) == set(first.uploaded)


def test_unpublishing_a_story_removes_its_assets_and_manifest_entry(
    tmp_path: Path, s3: S3Client
) -> None:
    settings = _settings(tmp_path)
    first_id = "story-one"
    second_id = "story-two"
    manifest = {
        "language": "it",
        "prompts": {"greeting": "https://cdn.example.test/published/prompts/it/greeting.mp3"},
        "stories": [{"id": first_id}, {"id": second_id}],
    }
    s3.put_object(
        Bucket=BUCKET,
        Key="published/it/manifest.json",
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{first_id}/story.json", Body=b"story")
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{first_id}/p1.mp3", Body=b"audio")
    s3.put_object(Bucket=BUCKET, Key="published/prompts/it/greeting.mp3", Body=b"prompt")

    unpublish_story(first_id, settings, client=s3)
    unpublish_story(first_id, settings, client=s3)

    assert _keys(s3, prefix=f"published/stories/{first_id}/") == []
    assert [entry["id"] for entry in _manifest(s3)["stories"]] == [second_id]
    assert _keys(s3, prefix="published/prompts/it/")


def test_unpublish_handles_a_manifest_without_a_stories_array(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)
    story_id = "story-one"
    s3.put_object(
        Bucket=BUCKET,
        Key="published/it/manifest.json",
        Body=json.dumps({"language": "it"}).encode(),
        ContentType="application/json",
    )
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/story.json", Body=b"story")

    unpublish_story(story_id, settings, client=s3)

    assert _keys(s3, prefix=f"published/stories/{story_id}/") == []


def test_unpublish_updates_manifest_before_deleting_assets(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    story_id = "story-one"
    manifest = {"language": "it", "stories": [{"id": story_id}]}
    s3.put_object(
        Bucket=BUCKET,
        Key="published/it/manifest.json",
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/story.json", Body=b"story")
    events: list[str] = []
    put_object = s3.put_object
    delete_objects = s3.delete_objects

    def record_put_object(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Key"] == "published/it/manifest.json":
            events.append("manifest")
        return put_object(**kwargs)

    def record_delete_objects(**kwargs: Any) -> dict[str, Any]:
        events.append("assets")
        return delete_objects(**kwargs)

    monkeypatch.setattr(s3, "put_object", record_put_object)
    monkeypatch.setattr(s3, "delete_objects", record_delete_objects)

    unpublish_story(story_id, settings, client=s3)

    assert events == ["manifest", "assets"]


def test_publishing_a_second_story_appends_to_the_manifest(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path)
    first = _assembled(tmp_path, story_id="story-one", title="Prima")
    second = _assembled(tmp_path, story_id="story-two", title="Seconda")
    stage_story(first, settings, client=s3)
    stage_story(second, settings, client=s3)
    _stage_prompts(s3)

    publish_story("story-one", settings, client=s3)
    result = publish_story("story-two", settings, client=s3)

    assert sorted(result.manifest_story_ids) == ["story-one", "story-two"]
    titles = {entry["id"]: entry["title"] for entry in _manifest(s3)["stories"]}
    assert titles == {"story-one": "Prima", "story-two": "Seconda"}


def test_republishing_the_same_id_replaces_its_entry_rather_than_duplicating(
    tmp_path: Path, s3: S3Client
) -> None:
    settings = _settings(tmp_path)
    stage_story(_assembled(tmp_path, story_id="story-one", title="Prima"), settings, client=s3)
    publish_story("story-one", settings, client=s3)

    stage_story(
        _assembled(tmp_path, story_id="story-one", title="Prima riveduta"), settings, client=s3
    )
    result = publish_story("story-one", settings, client=s3)

    assert result.manifest_story_ids == ["story-one"]
    assert [entry["title"] for entry in _manifest(s3)["stories"]] == ["Prima riveduta"]


def test_the_published_story_json_conforms_to_the_content_rules(
    tmp_path: Path, s3: S3Client
) -> None:
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    publish_story(assembled.story.id, settings, client=s3)

    key = f"published/stories/{assembled.story.id}/story.json"
    published = Story.model_validate(
        json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    )
    assert check_story(published) == []
