"""Behavior specs for published-story CRUD routes (operator library + parent deletes).

The operator sees everything live on R2 and can delete any story, bundled
launch content included. Parents see only their own family's approved packs
and get the same single destructive action. All S3 traffic runs on moto;
Clerk sessions are minted locally against a mock JWKS.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from src.api import auth as auth_mod
from src.api.auth import SESSION_COOKIE
from src.api.main import create_app
from src.api.routes.workshop import get_run_manager
from src.config import get_settings
from src.workshop.manager import RunManager
from src.workshop.records import PackRequest, RunRecord, RunStore, new_run
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    now,
)

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"

FAMILY = "a" * 32


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


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


def _put_assets(s3: S3Client, story_id: str) -> None:
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/story.json", Body=b"{}")
    s3.put_object(Bucket=BUCKET, Key=f"published/stories/{story_id}/p1.webp", Body=b"webp:p1")


def _asset_keys(s3: S3Client, story_id: str) -> list[str]:
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"published/stories/{story_id}/")
    return [item["Key"] for item in response.get("Contents", [])]


def _manifest(s3: S3Client, language: str) -> dict[str, Any]:
    body = s3.get_object(Bucket=BUCKET, Key=f"published/{language}/manifest.json")["Body"].read()
    return dict(json.loads(body))


def _approved_run(store: RunStore, family_token: str, story_ids: list[str]) -> RunRecord:
    record = new_run(family_token, PackRequest(theme="first_snow", language="it", count=1))
    record = record.advance("running").advance("staged").advance("approved", story_ids=story_ids)
    store.save(record)
    return record


class Harness:
    def __init__(self, tmp_path: Path, s3: S3Client) -> None:
        self.key = generate_rsa_keypair()
        auth_mod._fetch_jwks = make_mock_fetch(self.key)  # type: ignore[assignment]
        auth_mod._jwks_state.keys = None
        auth_mod._jwks_state.fetched_at = 0.0
        self.settings = clerk_settings(clerk_issuer="https://test.clerk.test").model_copy(
            update={"r2_bucket": BUCKET, "content_dir": tmp_path / "content"}
        )
        self.store = RunStore(self.settings, client=s3)
        self.manager = RunManager(self.store, self.settings, generate_pack=lambda request, s: [])
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        self.client = TestClient(app, base_url="https://testserver")

    def sign_in(self, claims: dict[str, Any]) -> None:
        payload = {
            **claims,
            "iat": now(),
            "nbf": now(),
            "exp": now() + 3600,
            "iss": "https://test.clerk.test",
        }
        self.client.cookies.set(SESSION_COOKIE, mint_token(self.key, payload))


OPERATOR: dict[str, Any] = {"sub": "user_op", "role": "operator"}
PARENT: dict[str, Any] = {"sub": "user_parent", "family_token": FAMILY}


def test_the_library_lists_every_published_story(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta"), ("neve-it-1", "Prima neve")])
    _put_manifest(s3, "es", [("mar-es-1", "El mar")])
    s3.put_object(Bucket=BUCKET, Key="published/stories/orphan-1/story.json", Body=b"{}")
    harness = Harness(tmp_path, s3)
    harness.sign_in(OPERATOR)

    page = harness.client.get("/workshop/library")

    assert page.status_code == 200
    assert "La barchetta" in page.text
    assert "Prima neve" in page.text
    assert "El mar" in page.text
    assert "orphan-1" in page.text


def test_the_library_redirects_non_operators_to_parent(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    harness.sign_in(PARENT)

    response = harness.client.get("/workshop/library", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/parent"


def test_the_library_asks_for_sign_in_when_unauthenticated(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)

    page = harness.client.get("/workshop/library")

    assert page.status_code == 200
    assert 'id="clerk-signin"' in page.text


def test_an_operator_deletes_any_published_story_forever(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_assets(s3, "sea-it-1")
    harness = Harness(tmp_path, s3)
    harness.sign_in(OPERATOR)

    response = harness.client.post(
        "/workshop/stories/sea-it-1/delete",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.text == ""
    assert _manifest(s3, "it")["stories"] == []
    assert _asset_keys(s3, "sea-it-1") == []


def test_a_non_operator_cannot_delete_via_the_workshop(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta")])
    _put_assets(s3, "sea-it-1")
    harness = Harness(tmp_path, s3)
    harness.sign_in(PARENT)

    response = harness.client.post("/workshop/stories/sea-it-1/delete")

    assert response.status_code == 403
    assert len(_asset_keys(s3, "sea-it-1")) == 2


def test_a_parent_sees_only_own_approved_packs(tmp_path: Path, s3: S3Client) -> None:
    _put_manifest(s3, "it", [("sea-it-1", "La barchetta"), ("neve-it-1", "Prima neve")])
    harness = Harness(tmp_path, s3)
    _approved_run(harness.store, FAMILY, ["sea-it-1"])
    harness.sign_in(PARENT)

    page = harness.client.get("/parent/stories")

    assert page.status_code == 200
    assert "La barchetta" in page.text
    assert "Prima neve" not in page.text


def test_a_signed_out_parent_gets_the_sign_in_page(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)

    page = harness.client.get("/parent/stories")

    assert page.status_code == 200
    assert 'id="clerk-sign-in"' in page.text
