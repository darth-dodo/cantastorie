"""Parent stories-tab tests (AI-445): run labels, delete arming, ownership guard."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
import src.api.routes.parent as parent_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import get_run_manager
from src.api.routes.parent import router as parent_router
from src.config import get_settings
from src.pipeline.publish import PublishedStory
from src.workshop.records import RunRecord, StoryRequest, new_run
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
    req = StoryRequest(theme="the_sleepy_sea", language="it")
    run = new_run(family_token, req)
    run = run.advance("running")
    run = run.advance("staged", story_id=story_id)
    return run


def _make_approved_run(family_token: str = VALID_TOKEN, story_id: str = "story-abc") -> RunRecord:
    req = StoryRequest(theme="the_sleepy_sea", language="it")
    run = new_run(family_token, req)
    run = run.advance("running")
    run = run.advance("staged", story_id=story_id)
    run = run.advance("approved")
    return run


FAMILY_STORY = PublishedStory(
    id="story-abc",
    title="La barchetta",
    language="it",
    cover="https://r2.example/cover.webp",
    family_token=VALID_TOKEN,
)


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    stories: list[PublishedStory] | None = None,
    family_token: str = VALID_TOKEN,
    unpublish_fn: Any = None,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    monkeypatch.setattr(
        parent_module, "list_family_shelf", lambda settings, family_token: stories or []
    )
    if unpublish_fn is not None:
        monkeypatch.setattr(parent_module, "unpublish_story", unpublish_fn)
    else:
        monkeypatch.setattr(parent_module, "unpublish_story", lambda *a, **kw: None)
    settings = clerk_settings(clerk_issuer=ISSUER)
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_run_manager] = lambda: manager
    token = mint_token(private_key, valid_payload(family_token=family_token, iss=ISSUER))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


# ── Label ──────────────────────────────────────────────────────────────────────


def test_staged_label_reads_needs_your_eyes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The packs page must show 'needs your eyes' for staged runs (not 'staged — review')."""
    run = _make_staged_run()
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent")
    assert r.status_code == 200
    assert "needs your eyes" in r.text


# ── Delete arming ─────────────────────────────────────────────────────────────


def test_family_story_delete_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stories page must include 'Delete for good?' arming text for owned stories."""
    run = _make_approved_run(story_id="story-abc")
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager, stories=[FAMILY_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "Delete for good?" in r.text


# ── Ownership guard ────────────────────────────────────────────────────────────


def test_delete_rejects_non_owned_story(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting a story not owned by the authenticated family returns 404."""
    # Manager has no approved runs → _owned_story_ids returns empty set
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.post("/parent/stories/not-owned-story/delete", follow_redirects=False)
    assert r.status_code in (403, 404)
