"""Behavior specs for listing what is published (story CRUD).

The library pages read the bucket, not a database: every manifest under
published/{lang}/manifest.json contributes its story entries, and story
directories no manifest lists are surfaced separately as orphans. All S3
traffic is served by moto's in-memory bucket: zero network.
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
from src.pipeline.publish import list_orphan_story_dirs, list_published_stories

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"


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


def _put_manifest(s3: S3Client, language: str, stories: list[tuple[str, str]]) -> None:
    entries: list[dict[str, Any]] = [
        {
            "id": story_id,
            "title": title,
            "wash": "wash-barchetta",
            "story": f"{PUBLIC_BASE}/stories/{story_id}/story.json",
            "cover": f"{PUBLIC_BASE}/stories/{story_id}/p1.webp",
        }
        for story_id, title in stories
    ]
    body = json.dumps({"language": language, "prompts": {}, "stories": entries}).encode()
    s3.put_object(Bucket=BUCKET, Key=f"published/{language}/manifest.json", Body=body)


def test_lists_stories_across_languages(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_manifest(s3, "es", [("mar-es-1", "El mar")])

    stories = list_published_stories(_settings(tmp_path))

    assert [(s.id, s.language, s.title) for s in stories] == [
        ("mar-es-1", "es", "El mar"),
        ("sea-it-1", "it", "La barchetta"),
    ]


def test_an_empty_bucket_lists_nothing(tmp_path: Path, s3: S3Client) -> None:
    assert list_published_stories(_settings(tmp_path)) == []


def test_flags_directories_no_manifest_lists(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    s3.put_object(Bucket=BUCKET, Key="published/stories/ghost/story.json", Body=b"{}")

    assert list_orphan_story_dirs(_settings(tmp_path)) == ["ghost"]


FAMILY = "a" * 32
OTHER_FAMILY = "b" * 32


def _put_overlay_manifest(
    s3: S3Client, family_token: str, language: str, stories: list[tuple[str, str]]
) -> None:
    entries: list[dict[str, Any]] = [
        {
            "id": story_id,
            "title": title,
            "wash": "wash-barchetta",
            "story": f"{PUBLIC_BASE}/families/{family_token}/stories/{story_id}/story.json",
            "cover": f"{PUBLIC_BASE}/families/{family_token}/stories/{story_id}/p1.webp",
        }
        for story_id, title in stories
    ]
    body = json.dumps({"language": language, "prompts": {}, "stories": entries}).encode()
    s3.put_object(
        Bucket=BUCKET, Key=f"published/families/{family_token}/{language}/manifest.json", Body=body
    )


def test_lists_shared_and_family_overlay_stories_tagged_by_owner(
    tmp_path: Path, s3: S3Client
) -> None:
    _put_manifest(s3, "it", [("global-it-1", "Globale")])
    _put_overlay_manifest(s3, FAMILY, "it", [("fam-a-it-1", "Storia A")])
    _put_overlay_manifest(s3, OTHER_FAMILY, "it", [("fam-b-it-1", "Storia B")])

    stories = list_published_stories(_settings(tmp_path))

    tags = {s.id: s.family_token for s in stories}
    assert tags == {
        "global-it-1": None,  # shared shelf → no owning family
        "fam-a-it-1": FAMILY,
        "fam-b-it-1": OTHER_FAMILY,
    }


def test_overlay_manifests_are_not_double_counted_as_shared(tmp_path: Path, s3: S3Client) -> None:
    """A family manifest must never be read as a shared-shelf language."""
    _put_overlay_manifest(s3, FAMILY, "it", [("fam-a-it-1", "Storia A")])

    stories = list_published_stories(_settings(tmp_path))

    assert [s.id for s in stories] == ["fam-a-it-1"]
    assert stories[0].family_token == FAMILY
    assert stories[0].language == "it"  # not "families/{token}/it"
