"""Test that approving a pack redirects to /parent/stories (AI-445)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
import src.api.routes.parent as parent_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import get_family_publisher, get_run_manager
from src.api.routes.parent import router as parent_router
from src.config import get_settings
from src.workshop.records import RunRecord, StoryRequest, new_run
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

VALID_TOKEN = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
ISSUER = "https://test.clerk.test"


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


def _make_staged_run() -> RunRecord:
    req = StoryRequest(theme="the_sleepy_sea", language="it")
    run = new_run(VALID_TOKEN, req)
    run = run.advance("running")
    run = run.advance("staged", story_id="story-abc")
    return run


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings(clerk_issuer=ISSUER)
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_run_manager] = lambda: manager
    # no-op publisher: just records approval without touching R2
    app.dependency_overrides[get_family_publisher] = lambda: lambda story_id, family_token: None
    token = mint_token(private_key, valid_payload(family_token=VALID_TOKEN, iss=ISSUER))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_approve_lands_on_your_stories(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approving a staged pack must redirect to /parent/stories, not /parent."""
    run = _make_staged_run().mark_reviewed()  # approve follows review (B2)
    monkeypatch.setattr(parent_module, "_staged_story_exists", lambda settings, story_id: True)
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager)
    r = client.post(f"/parent/packs/{run.id}/approve", follow_redirects=False)
    assert r.headers["location"] == "/parent/stories"
