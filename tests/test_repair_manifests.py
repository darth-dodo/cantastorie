import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from scripts.repair_manifests import repair_manifests

from src.config import Settings

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"
STORY_ID = "sleepy-sea"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        r2_endpoint_url="https://r2.example.test",
        r2_access_key_id="access-key",
        r2_secret_access_key="secret-key",
        r2_bucket=BUCKET,
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
