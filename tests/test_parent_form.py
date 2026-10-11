"""Parent form UI tests (AI-445): Custom disclosure, 4-min note, capped form state."""

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
from src.workshop.manager import GLOBAL_CAP_MESSAGE, RunCapExceeded
from src.workshop.records import StoryRequest, new_run
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


class _FakeManager:
    """Minimal fake manager for rendering tests."""

    def __init__(
        self,
        raise_cap: RunCapExceeded | None = None,
        service_cap: RunCapExceeded | None = None,
    ) -> None:
        self.raise_cap = raise_cap
        self.service_cap = service_cap
        self.submits: list[tuple[str, Any]] = []

        class _FakeStore:
            def list_runs(self, *, family_token: str | None = None, state: Any = None) -> list[Any]:
                return []

        self.store = _FakeStore()

    async def submit(self, family_token: str, request: Any) -> Any:
        if self.raise_cap is not None:
            raise self.raise_cap
        self.submits.append((family_token, request))
        return new_run(family_token, request)

    async def execute(self, record: Any) -> Any:
        return record

    def global_cap(self) -> RunCapExceeded | None:
        return self.service_cap


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    family_token: str = VALID_TOKEN,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    monkeypatch.setattr(parent_module, "list_family_shelf", lambda settings, family_token: [])
    settings = clerk_settings(clerk_issuer=ISSUER)
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_run_manager] = lambda: manager
    token = mint_token(private_key, valid_payload(family_token=family_token, iss=ISSUER))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_make_screen_shows_the_premise_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The make screen always shows the optional premise field (AI-503)."""
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    assert 'name="premise"' in r.text


def test_four_minute_note_before_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """The make screen includes the four-minute note."""
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent/make")
    assert r.status_code == 200
    assert "four minutes" in r.text


def test_cap_state_dims_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cap_message is set (via POST to /parent/runs), the Being made page shows the cap message."""
    active = new_run(VALID_TOKEN, StoryRequest(theme="the_sleepy_sea", language="it"))
    running = active.advance("running")
    manager = _FakeManager(
        raise_cap=RunCapExceeded("a story is already being made", active=running)
    )
    client = _make_client(monkeypatch, manager)
    # POST triggers the cap branch which re-renders being_made.html with cap_message set
    r = client.post(
        "/parent/runs",
        data={"theme": "the_sleepy_sea", "language": "it"},
    )
    assert r.status_code == 200
    assert "already being made" in r.text  # cap message shown


def test_the_make_screen_shows_the_service_wide_cap_up_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H4: once the whole service has used up the day, the make screen says so
    before the parent fills in the form, as it does for the family's own cap."""
    manager = _FakeManager(service_cap=RunCapExceeded(GLOBAL_CAP_MESSAGE, service_wide=True))
    client = _make_client(monkeypatch, manager)

    r = client.get("/parent/make")

    assert r.status_code == 200
    assert "the story workshop is resting for today" in r.text


def test_premise_over_the_bound_is_rejected_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI-470: a direct POST past the textarea's maxlength gets a 422 with a
    plain-language message, not a raw JSON error dump."""
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)

    r = client.post(
        "/parent/runs",
        data={"theme": "the_sleepy_sea", "language": "it", "premise": "x" * 301},
    )

    assert r.status_code == 422
    assert "too long" in r.text
    assert '"loc"' not in r.text


def test_premise_at_the_bound_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)

    r = client.post(
        "/parent/runs",
        data={"theme": "the_sleepy_sea", "language": "it", "premise": "x" * 300},
        follow_redirects=False,
    )

    assert r.status_code == 303


# AI-503: a story request is a theme AND an optional premise, plus a shape.
# The #96 "Custom…" pseudo-theme posted theme=custom (not a Theme literal) and
# disabled the premise whenever a real theme was picked, so neither worked.


def test_make_form_offers_premise_alongside_a_real_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, _FakeManager())
    page = client.get("/parent/make").text
    assert 'value="custom"' not in page  # no pseudo-theme that fails validation
    assert 'name="premise"' in page
    premise_tag = page[page.index('name="premise"') - 200 : page.index('name="premise"') + 200]
    assert "disabled" not in premise_tag
    assert "data-custom-premise" not in page


def test_make_form_offers_a_linear_or_branching_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _make_client(monkeypatch, _FakeManager()).get("/parent/make").text
    assert 'name="shape"' in page
    assert 'value="linear"' in page
    assert 'value="branching"' in page


@pytest.mark.parametrize("shape", ["linear", "branching"])
def test_a_parent_request_carries_theme_premise_and_shape(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    response = client.post(
        "/parent/runs",
        data={
            "theme": "the_sleepy_sea",
            "language": "it",
            "premise": "a small boat wants to see the moon",
            "shape": shape,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    [(_, request)] = manager.submits
    assert request.theme == "the_sleepy_sea"
    assert request.premise == "a small boat wants to see the moon"
    assert request.shape == shape


def test_a_parent_request_with_an_unknown_shape_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    response = client.post(
        "/parent/runs",
        data={"theme": "the_sleepy_sea", "language": "it", "shape": "spiral"},
        follow_redirects=False,
    )
    assert response.status_code in (400, 422)
    assert manager.submits == []
