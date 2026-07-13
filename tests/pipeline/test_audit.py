"""Behavior specs for the audit script (AI-378).

The audit verifies that every manifest entry resolves to approved content
under ``published/``, that no manifest URL points into ``pending/``, and
that no orphan story directory exists unlisted. All S3 traffic is served
by moto's in-memory bucket: zero network.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.config import Settings
from src.pipeline.models import Page, PageAudio, Story, Theme, WordTiming
from src.pipeline.publish import (
    STAGED_PREFIX,
    audit_published_bucket,
    publish_story,
    stage_story,
)
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
    language: str = "it",
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
        language=language,
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


def test_audit_passes_when_every_manifest_entry_resolves(tmp_path: Path, s3: S3Client) -> None:
    """Given a published story with all assets present,
    When audit runs,
    Then zero violations are reported.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)

    publish_story(assembled.story.id, settings, client=s3)

    result = audit_published_bucket(settings, client=s3)
    assert result.violations == []
    assert result.manifests_checked == 1


def test_audit_fails_when_a_manifest_references_a_missing_story_json(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a manifest entry whose story.json was deleted,
    When audit runs,
    Then a violation is reported naming the story.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)
    publish_story(assembled.story.id, settings, client=s3)

    s3.delete_object(Bucket=BUCKET, Key=f"published/stories/{assembled.story.id}/story.json")

    result = audit_published_bucket(settings, client=s3)
    assert len(result.violations) >= 1
    assert any("story.json missing" in v for v in result.violations)


def test_audit_fails_when_a_manifest_references_a_missing_asset(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a published story whose page audio was deleted,
    When audit runs,
    Then a violation is reported naming the missing audio.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)
    publish_story(assembled.story.id, settings, client=s3)

    first_audio = assembled.story.pages[0].audio.file
    s3.delete_object(Bucket=BUCKET, Key=f"published/stories/{assembled.story.id}/{first_audio}")

    result = audit_published_bucket(settings, client=s3)
    assert any("missing audio" in v for v in result.violations)


def test_audit_fails_when_a_manifest_entry_points_into_pending(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a manifest entry with a URL pointing into pending/,
    When audit runs,
    Then a violation is reported naming pending/.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)
    publish_story(assembled.story.id, settings, client=s3)

    manifest = json.loads(
        s3.get_object(Bucket=BUCKET, Key="published/it/manifest.json")["Body"].read()
    )
    manifest["stories"][0]["story"] = "https://cdn.example.test/pending/staged/evil/story.json"
    s3.put_object(
        Bucket=BUCKET,
        Key="published/it/manifest.json",
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )

    result = audit_published_bucket(settings, client=s3)
    assert any("pending/" in v for v in result.violations)


def test_audit_fails_when_a_story_directory_exists_but_is_not_listed_in_any_manifest(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given an orphan story directory under published/stories/ not in any manifest,
    When audit runs,
    Then a violation is reported naming the orphan.
    """
    settings = _settings(tmp_path)
    assembled = _assembled(tmp_path)
    stage_story(assembled, settings, client=s3)
    _stage_prompts(s3)
    publish_story(assembled.story.id, settings, client=s3)

    s3.put_object(
        Bucket=BUCKET,
        Key="published/stories/orphan-story/story.json",
        Body=b"{}",
        ContentType="application/json",
    )

    result = audit_published_bucket(settings, client=s3)
    assert any("orphan-story" in v for v in result.violations)


def test_audit_reports_manifests_checked_count(tmp_path: Path, s3: S3Client) -> None:
    """Given stories published in two languages,
    When audit runs,
    Then the manifests_checked count is 2.
    """
    settings = _settings(tmp_path)
    assembled_it = _assembled(tmp_path, story_id="sea-it", title="Mare", language="it")
    stage_story(assembled_it, settings, client=s3)
    _stage_prompts(s3, language="it")
    publish_story("sea-it", settings, client=s3)

    assembled_en = _assembled(tmp_path, story_id="sea-en", title="Sea", language="en")
    stage_story(assembled_en, settings, client=s3)
    _stage_prompts(s3, language="en")
    publish_story("sea-en", settings, client=s3)

    result = audit_published_bucket(settings, client=s3)
    assert result.manifests_checked == 2
