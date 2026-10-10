"""Parent redesign tests (AI-460): /parent/make route, tab counts, shared stories, empty state."""

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

    def reap_stale(self, runs: Any = None) -> list[RunRecord]:
        return []

    def global_cap(self) -> None:
        return None


def _make_queued_run(family_token: str = VALID_TOKEN) -> RunRecord:
    req = StoryRequest(theme="the_sleepy_sea", language="it")
    return new_run(family_token, req)


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

SHARED_STORY = PublishedStory(
    id="shared-xyz",
    title="The Forest Friends",
    language="en",
    cover="https://r2.example/shared-cover.webp",
    family_token=None,  # shared shelf
)


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    stories: list[PublishedStory] | None = None,
    family_token: str = VALID_TOKEN,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    monkeypatch.setattr(
        parent_module, "list_family_shelf", lambda settings, family_token: stories or []
    )
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


# ── /parent/make route ────────────────────────────────────────────────────────


def test_make_route_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/make returns 200 with the form."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200


def test_make_route_has_premise_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/make includes the optional premise field, offered alongside
    the theme rather than behind a "Custom…" theme (AI-503)."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    assert 'name="premise"' in r.text


def test_make_route_has_make_our_story_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/make includes the 'Make our story' submit button."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    assert "Make our story" in r.text


def test_make_route_has_back_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/make includes a back navigation element."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    # Back button should link back to /parent/stories
    assert "/parent/stories" in r.text


def test_make_route_has_four_minute_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent/make includes the 'four minutes' note card."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    assert "four minutes" in r.text


# ── Being made tab no longer contains the form ────────────────────────────────


def test_being_made_tab_does_not_have_make_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /parent (Being made tab) does NOT contain the make-a-story form."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent")
    assert r.status_code == 200
    # The form's action target must NOT appear in the Being Made tab
    assert 'action="/parent/runs"' not in r.text
    # The premise field must NOT appear here
    assert 'name="premise"' not in r.text


# ── Tab counts ────────────────────────────────────────────────────────────────


def test_stories_tab_shows_owned_story_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Your-stories tab shows the count of published family stories."""
    run = _make_approved_run(story_id="story-abc")
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager, stories=[FAMILY_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    # Tab count "· 1" should appear
    assert "· 1" in r.text


def test_being_made_tab_shows_inflight_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Being-made tab shows the count of in-flight runs."""
    run = _make_queued_run()
    manager = _FakeManager({run.id: run})
    client = _make_client(monkeypatch, manager, stories=[])
    r = client.get("/parent")
    assert r.status_code == 200
    # The "Being made" tab must have a count (· 1)
    assert "· 1" in r.text


# ── Empty state ───────────────────────────────────────────────────────────────


def test_stories_empty_state_shown_when_no_owned_stories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the family has 0 owned stories, the empty-state card is shown."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "No stories yet" in r.text


def test_stories_empty_state_muted_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty state shows the muted 'shared stories below' line."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "shared stories" in r.text.lower() or "Shared" in r.text


# ── Shared stories section ────────────────────────────────────────────────────


def test_shared_stories_section_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Your-stories shows the shared section with operator stories."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[SHARED_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    # Shared section heading
    assert "Shared with every family" in r.text


def test_shared_story_title_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared story's title is rendered."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[SHARED_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "The Forest Friends" in r.text


def test_shared_story_has_shared_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared story card shows the 'shared' badge."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[SHARED_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "shared" in r.text.lower()


def test_shared_story_no_delete_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared stories do not have a delete button."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[SHARED_STORY])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    # shared-xyz should NOT appear as a delete target
    assert "shared-xyz/delete" not in r.text


def test_make_a_story_cta_links_to_make_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The + Make a story CTA on Your-stories links to /parent/make."""
    manager = _FakeManager({})
    client = _make_client(monkeypatch, manager, stories=[])
    r = client.get("/parent/stories")
    assert r.status_code == 200
    assert "/parent/make" in r.text


# ── Sort: Newest is by publish time, not by id ────────────────────────────────


def test_sort_newest_orders_by_approval_time_not_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ids sort a < b < c, but c was published first and a last: newest = a, b, c
    would be the id-reversal bug's c, b, a."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    base = datetime(2026, 9, 1, tzinfo=UTC)
    runs = {}
    stories = []
    for story_id, days in (("a-story", 3), ("b-story", 2), ("c-story", 1)):
        run = _make_approved_run(story_id=story_id).model_copy(
            update={"updated_at": base + timedelta(days=days)}
        )
        runs[run.id] = run
        stories.append(
            PublishedStory(
                id=story_id, title=story_id, language="it", cover="", family_token=VALID_TOKEN
            )
        )
    client = _make_client(monkeypatch, _FakeManager(runs), stories=stories)
    text = client.get("/parent/stories").text
    assert text.index('data-story-id="a-story"') < text.index('data-story-id="b-story"')
    assert text.index('data-story-id="b-story"') < text.index('data-story-id="c-story"')


# ── Make screen: the one-story-at-a-time state is designed, up front ─────────


def test_make_screen_shows_cap_card_and_inert_form_while_a_story_is_cooking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _make_queued_run()
    client = _make_client(monkeypatch, _FakeManager({run.id: run}))
    text = client.get("/parent/make").text
    assert "data-cap-message" in text
    assert "One story at a time" in text
    assert "ws-form--capped" in text
    assert 'type="submit" class="ws-pill ws-pill-accent" disabled' in text


def test_make_screen_is_open_when_nothing_is_cooking(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, _FakeManager({}))
    text = client.get("/parent/make").text
    assert "data-cap-message" not in text
    assert "ws-form--capped" not in text


@pytest.mark.parametrize("path", ["/parent/make", "/parent/stories"])
def test_parent_pages_sign_in_through_the_parent_door(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Signed out, every parent page's sign-in returns to the parent area."""
    client = _make_client(monkeypatch, _FakeManager({}))
    client.cookies.clear()
    assert 'data-auth-door="parent"' in client.get(path).text
