"""The unauthenticated /published/{path} proxy (H7, AI-471)."""

from collections.abc import Iterator
from urllib.parse import unquote

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.api.main import create_app
from src.config import Settings, get_settings

BUCKET = "cantastorie-test"
TOKEN = "0123456789abcdef0123456789abcdef"
IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def client(s3: S3Client) -> TestClient:
    settings = Settings(
        _env_file=None,
        r2_endpoint_url="https://s3.us-east-1.amazonaws.com",
        r2_access_key_id="testing",
        r2_secret_access_key="testing",
        r2_bucket=BUCKET,
        r2_pending_bucket="cantastorie-pending-test",
        r2_public_base="/published",
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _put(s3: S3Client, key: str, body: bytes, **extra: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("en/manifest.json", "application/json"),
        ("stories/pip-the-pirate-en-0397c7d0/story.json", "application/json"),
        ("stories/pip-the-pirate-en-0397c7d0/p1.3f9a1c2b.mp3", "audio/mpeg"),
        ("stories/pip-the-pirate-en-0397c7d0/p1.3f9a1c2b.wav", "audio/wav"),
        ("stories/pip-the-pirate-en-0397c7d0/p1.3f9a1c2b.webp", "image/webp"),
        ("prompts/it/shelf_greeting.abc123.mp3", "audio/mpeg"),
        (f"families/{TOKEN}/en/manifest.json", "application/json"),
        (f"families/{TOKEN}/stories/bear-en-00000001/p2.aa11bb22.webp", "image/webp"),
        (f"families/{TOKEN}/prompts/en/end_prompt.abc123.mp3", "audio/mpeg"),
    ],
)
def test_serves_every_shape_the_player_requests(
    client: TestClient, s3: S3Client, path: str, content_type: str
) -> None:
    _put(s3, f"published/{path}", b"payload")

    r = client.get(f"/published/{path}")

    assert r.status_code == 200
    assert r.content == b"payload"
    assert r.headers["content-type"].startswith(content_type)


def test_streams_a_body_larger_than_one_chunk(client: TestClient, s3: S3Client) -> None:
    body = bytes(range(256)) * 4096  # 1 MiB, several iter_chunks() reads
    _put(s3, "published/stories/big-en-00000001/p1.deadbeef.mp3", body)

    r = client.get("/published/stories/big-en-00000001/p1.deadbeef.mp3")

    assert r.status_code == 200
    assert r.content == body


def test_reemits_upstream_cache_control_and_etag(client: TestClient, s3: S3Client) -> None:
    key = "published/stories/pip-en-00000001/p1.3f9a1c2b.webp"
    _put(s3, key, b"art", CacheControl=IMMUTABLE, ContentType="image/webp")
    etag = s3.head_object(Bucket=BUCKET, Key=key)["ETag"]

    r = client.get("/published/stories/pip-en-00000001/p1.3f9a1c2b.webp")

    assert r.status_code == 200
    assert r.headers["cache-control"] == IMMUTABLE
    assert r.headers["etag"] == etag
    assert r.headers["content-length"] == "3"


def test_missing_key_is_a_generic_404(client: TestClient) -> None:
    r = client.get("/published/stories/nope-en-00000001/story.json")

    assert r.status_code == 404
    assert "NoSuchKey" not in r.text
    assert "GetObject" not in r.text
    assert BUCKET not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/pending/staged/secret/story.json",
        "%2E%2E/pending/staged/secret/story.json",
        "stories/%2e%2e/%2e%2e/pending/staged/secret/story.json",
        "stories/x/%2e%2e%2f%2e%2e%2fpending/staged/secret/story.json",
        "stories/x/..%2f..%2fpending%2fstaged%2fsecret%2fstory.json",
        "stories/%2e/x/story.json",
        "%2fpending/staged/secret/story.json",
        "/pending/staged/secret/story.json",
        "stories//story.json",
        "stories/x/.story.json",
        "stories/x/%5c..%5cstory.json",
        "pending/staged/secret/story.json",
        "families/NOT-A-TOKEN/en/manifest.json",
        f"families/{TOKEN.upper()}/en/manifest.json",
        f"families/{TOKEN}/",
        "families/",
        "manifest.json",
        "english/manifest.json",
        "en/manifest.json.bak",
        "stories/x/story.html",
        "stories/x/sub/story.json",
    ],
)
def test_rejects_paths_outside_the_allowlist(client: TestClient, s3: S3Client, path: str) -> None:
    # Plant the object at the exact key an unguarded proxy would build, so the
    # test proves the allowlist refuses it rather than trusting the storage edge
    # to normalize dot segments.
    _put(s3, f"published/{unquote(path)}", b"unapproved")
    _put(s3, "pending/staged/secret/story.json", b"unapproved")

    r = client.get(f"/published/{path}")

    assert r.status_code == 404
    assert b"unapproved" not in r.content


def test_unconfigured_r2_is_404() -> None:
    settings = Settings(_env_file=None, r2_bucket="")
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    r = TestClient(app).get("/published/en/manifest.json")

    assert r.status_code == 404
