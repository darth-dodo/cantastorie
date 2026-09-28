import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from scripts.repair_manifests import repair_manifests

from src.config import Settings

BUCKET = "cantastorie-published"
PENDING_BUCKET = "cantastorie-pending"
PUBLIC_BASE = "https://cdn.example.test/published"
STORY_ID = "sleepy-sea"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        r2_endpoint_url="https://r2.example.test",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_bucket=BUCKET,
        r2_pending_bucket=PENDING_BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _put_production_manifest(s3: S3Client) -> None:
    manifest = {
        "language": "it",
        "prompts": {},
        "stories": [
            {
                "id": STORY_ID,
                "title": "La barchetta",
                "wash": f"wash-{STORY_ID}",
                "story": f"/stories/{STORY_ID}/story.json",
                "cover": f"/stories/{STORY_ID}/cover.webp",
            }
        ],
    }
    story = {
        "id": STORY_ID,
        "language": "it",
        "title": "La barchetta",
        "theme": "the_sleepy_sea",
        "shape": "linear",
        "pages": [{"id": "p1", "text": "Ciao.", "image": "cover.webp"}],
    }
    s3.put_object(
        Bucket=BUCKET,
        Key="published/it/manifest.json",
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=f"published/stories/{STORY_ID}/story.json",
        Body=json.dumps(story).encode(),
        ContentType="application/json",
    )


def _manifest(s3: S3Client) -> dict[str, object]:
    body = s3.get_object(Bucket=BUCKET, Key="published/it/manifest.json")["Body"].read()
    return json.loads(body)


def test_repair_manifests_repairs_wash_and_relative_urls_idempotently(s3: S3Client) -> None:
    _put_production_manifest(s3)

    repaired = repair_manifests(_settings(), client=s3)

    assert repaired == ["published/it/manifest.json"]
    story = _manifest(s3)["stories"][0]
    assert story == {
        "id": STORY_ID,
        "title": "La barchetta",
        "wash": "wash-barchetta",
        "story": f"{PUBLIC_BASE}/stories/{STORY_ID}/story.json",
        "cover": f"{PUBLIC_BASE}/stories/{STORY_ID}/cover.webp",
    }
    assert repair_manifests(_settings(), client=s3) == []


def test_repair_manifests_dry_run_reports_repairs_without_writing(s3: S3Client) -> None:
    _put_production_manifest(s3)

    repaired = repair_manifests(_settings(), client=s3, dry_run=True)

    assert repaired == ["published/it/manifest.json"]
    story = _manifest(s3)["stories"][0]
    assert story["wash"] == f"wash-{STORY_ID}"
    assert story["story"] == f"/stories/{STORY_ID}/story.json"


def test_repair_manifests_writes_with_the_shared_ifmatch_cache_control_helper(
    s3: S3Client,
) -> None:
    """H5: repair must go through the same helper publish/unpublish use, not a
    bare put_object — so its write also carries the manifest's short TTL."""
    _put_production_manifest(s3)

    repair_manifests(_settings(), client=s3)

    manifest_obj = s3.get_object(Bucket=BUCKET, Key="published/it/manifest.json")
    assert manifest_obj["CacheControl"] == "public, max-age=60"


def test_repair_manifests_retries_a_conflict_without_losing_a_concurrent_publish(
    s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H5: a publish racing a repair must not silently lose either change. The
    repair write must carry IfMatch, so a concurrent publish landing between
    its read and its write causes a PreconditionFailed that it retries."""
    _put_production_manifest(s3)
    second_story = {
        "id": "second-story",
        "language": "it",
        "title": "Seconda",
        "theme": "the_sleepy_sea",
        "shape": "linear",
        "pages": [{"id": "p1", "text": "Ciao.", "image": "cover.webp"}],
    }
    s3.put_object(
        Bucket=BUCKET,
        Key="published/stories/second-story/story.json",
        Body=json.dumps(second_story).encode(),
        ContentType="application/json",
    )
    put_object = s3.put_object
    manifest_key = "published/it/manifest.json"
    raced = False

    def race_a_concurrent_publish(**kwargs: Any) -> dict[str, Any]:
        nonlocal raced
        if kwargs["Key"] == manifest_key and not raced:
            raced = True
            current = _manifest(s3)
            current["stories"].append(
                {
                    "id": "second-story",
                    "title": "Seconda",
                    "wash": "wash-barchetta",
                    "story": f"{PUBLIC_BASE}/stories/second-story/story.json",
                    "cover": f"{PUBLIC_BASE}/stories/second-story/cover.webp",
                }
            )
            put_object(
                Bucket=BUCKET,
                Key=manifest_key,
                Body=json.dumps(current).encode(),
                ContentType="application/json",
            )
        return put_object(**kwargs)

    monkeypatch.setattr(s3, "put_object", race_a_concurrent_publish)

    repair_manifests(_settings(), client=s3)

    ids = sorted(entry["id"] for entry in _manifest(s3)["stories"])
    assert ids == sorted([STORY_ID, "second-story"])
