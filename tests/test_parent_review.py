"""Parent staged-review page tests (AI-457).

Tests:
(a) GET /parent/staged/{story_id} for a family's own staged run → 200 + page text + Approve/Reject
(b) POST /parent/packs/{run_id}/approve from review redirects to /parent/stories
(c) A parent cannot review another family's staged run → 404
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import get_family_publisher, get_run_manager
from src.api.routes.parent import router as parent_router
from src.config import get_settings
from src.workshop.records import PackRequest, RunRecord, new_run
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

VALID_TOKEN = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
OTHER_TOKEN = "ffffffffffffffffffffffffffffffff"  # pragma: allowlist secret
ISSUER = "https://test.clerk.test"

STORY_DATA = {
    "id": "story-abc",
    "title": "The Sleepy Fox",
    "language": "en",
    "theme": "the_sleepy_sea",
    "shape": "linear",
    "pages": [
        {
            "id": "p1",
            "text": "Once upon a time there was a sleepy fox.",
            "image": None,
            "audio": None,
        },
        {
            "id": "p2",
            "text": "The fox fell asleep under the big oak tree.",
            "image": None,
            "audio": None,
        },
    ],
}


@pytest.fixture(autouse=True)
def reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module._jwks_state, "keys", None)
    monkeypatch.setattr(auth_module._jwks_state, "fetched_at", 0.0)


class _FakeStore:
    def __init__(self, runs: dict[str, RunRecord]) -> None:
        self._runs = runs

    def list_runs(self, *, family_token: str | None = None, state: Any = None) -> list[RunRecord]:
        result = list(self._runs.values())
        if family_token is not None:
            result = [r for r in result if r.family_token == family_token]
        if state is not None:
            result = [r for r in result if r.state == state]
        return result

    def load(self, family_token: str, run_id: str) -> RunRecord | None:
        rec = self._runs.get(run_id)
        if rec is None or rec.family_token != family_token:
            return None
        return rec

    def save(self, record: RunRecord) -> None:
        self._runs[record.id] = record


class _FakeManager:
    def __init__(self, runs: dict[str, RunRecord]) -> None:
        self.store = _FakeStore(runs)

    async def submit(self, family_token: str, request: Any) -> RunRecord:
        return new_run(family_token, request)

    async def execute(self, record: RunRecord) -> RunRecord:
        return record


def _make_staged_run(family_token: str = VALID_TOKEN, story_id: str = "story-abc") -> RunRecord:
    req = PackRequest(theme="the_sleepy_sea", language="en", count=1)
    run = new_run(family_token, req)
    run = run.advance("running")
    run = run.advance("staged", story_ids=[story_id])
    return run


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    family_token: str = VALID_TOKEN,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings(clerk_issuer=ISSUER)
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_run_manager] = lambda: manager
    app.dependency_overrides[get_family_publisher] = lambda: lambda story_id, family_token: None
    token = mint_token(private_key, valid_payload(family_token=family_token, iss=ISSUER))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


# ── (a) GET review page renders page text and Approve/Reject ────────────────


def test_parent_review_page_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/staged/{story_id} returns 200, renders page text, Approve & Reject."""
    run = _make_staged_run()
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager)

    fake_body = json.dumps(STORY_DATA).encode()
    mock_obj = {"Body": MagicMock(read=lambda: fake_body)}
    mock_client = MagicMock()
    mock_client.get_object.return_value = mock_obj

    with patch("src.api.routes.parent._build_client", return_value=mock_client):
        r = client.get(f"/parent/staged/story-abc?run={run.id}")

    assert r.status_code == 200
    assert "Once upon a time there was a sleepy fox." in r.text
    assert "Approve" in r.text
    assert "Reject" in r.text


# ── (b) Approve from review redirects to /parent/stories ───────────────────


def test_parent_review_approve_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /parent/packs/{run_id}/approve from review page redirects to /parent/stories."""
    run = _make_staged_run()
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager)

    r = client.post(f"/parent/packs/{run.id}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/parent/stories"


# ── (c) Parent cannot review another family's run → 404 ────────────────────


def test_parent_review_denies_other_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/staged/{story_id} for a different family's run returns 404."""
    # Run belongs to OTHER_TOKEN, but client is authenticated as VALID_TOKEN
    run = _make_staged_run(family_token=OTHER_TOKEN, story_id="story-other")
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager, family_token=VALID_TOKEN)

    r = client.get(f"/parent/staged/story-other?run={run.id}")
    assert r.status_code == 404
