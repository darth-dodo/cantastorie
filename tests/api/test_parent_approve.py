"""Behavior specs for parent approve → private overlay publish (Seam 2).

A family approving a staged pack publishes it to their own overlay lane
(published/families/{token}/…), never the shared shelf, and settles the run to
approved. Tenancy is enforced: a family cannot approve another family's run
(404), and the family_token is taken from the session, never the URL/form.

All S3 traffic runs on moto; Clerk sessions are minted locally against a mock
JWKS. Mirrors the Harness in tests/api/test_library_routes.py.
"""

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
from src.api.routes.parent import get_family_publisher
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

FAMILY = "a" * 32
OTHER_FAMILY = "b" * 32


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _staged_run(store: RunStore, family_token: str, story_ids: list[str]) -> RunRecord:
    record = new_run(family_token, PackRequest(theme="first_snow", language="it", count=1))
    record = record.advance("running").advance("staged", story_ids=story_ids)
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
        # Capture (story_id, family_token) pairs instead of touching R2.
        self.published: list[tuple[str, str | None]] = []
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        app.dependency_overrides[get_family_publisher] = (
            lambda: lambda story_id, family_token: self.published.append((story_id, family_token))
        )
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


PARENT: dict[str, Any] = {"sub": "user_parent", "family_token": FAMILY}


def test_approving_a_staged_pack_publishes_to_the_family_overlay(
    tmp_path: Path, s3: S3Client
) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, FAMILY, ["first_snow-it-fake0001"])
    harness.sign_in(PARENT)

    response = harness.client.post(f"/parent/packs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 303
    assert harness.published == [("first_snow-it-fake0001", FAMILY)]
    reloaded = harness.store.load(FAMILY, record.id)
    assert reloaded is not None
    assert reloaded.state == "approved"


def test_a_family_cannot_approve_another_familys_run(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    other = _staged_run(harness.store, OTHER_FAMILY, ["first_snow-it-fake0002"])
    harness.sign_in(PARENT)  # session = FAMILY

    response = harness.client.post(f"/parent/packs/{other.id}/approve", follow_redirects=False)

    assert response.status_code == 404
    assert harness.published == []
    reloaded = harness.store.load(OTHER_FAMILY, other.id)
    assert reloaded is not None
    assert reloaded.state == "staged"  # untouched


def test_approving_a_non_staged_run_does_not_publish(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    record = new_run(FAMILY, PackRequest(theme="first_snow", language="it", count=1))
    harness.store.save(record.advance("running"))  # still running, not staged
    harness.sign_in(PARENT)

    response = harness.client.post(f"/parent/packs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 400
    assert harness.published == []


def test_approve_requires_a_signed_in_parent(tmp_path: Path, s3: S3Client) -> None:
    harness = Harness(tmp_path, s3)
    record = _staged_run(harness.store, FAMILY, ["first_snow-it-fake0003"])

    response = harness.client.post(f"/parent/packs/{record.id}/approve", follow_redirects=False)

    assert response.status_code == 401
    assert harness.published == []
