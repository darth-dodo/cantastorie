"""Parent form UI tests (AI-445): Custom disclosure, 4-min note, capped form state."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import get_run_manager
from src.api.routes.parent import router as parent_router
from src.config import get_settings
from src.workshop.manager import RunCapExceeded
from src.workshop.records import PackRequest, new_run
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

    def __init__(self, raise_cap: RunCapExceeded | None = None) -> None:
        self.raise_cap = raise_cap

        class _FakeStore:
            def list_runs(self, *, family_token: str | None = None, state: Any = None) -> list[Any]:
                return []

        self.store = _FakeStore()

    async def submit(self, family_token: str, request: Any) -> Any:
        if self.raise_cap is not None:
            raise self.raise_cap
        return new_run(family_token, request)

    async def execute(self, record: Any) -> Any:
        return record


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
    token = mint_token(private_key, valid_payload(family_token=family_token, iss=ISSUER))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_custom_theme_reveals_premise_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The packs page includes the hidden premise container (data-custom-premise)."""
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent")
    assert r.status_code == 200
    assert "data-custom-premise" in r.text  # hidden field container present


def test_four_minute_note_before_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """The packs page includes the four-minute note."""
    manager = _FakeManager()
    client = _make_client(monkeypatch, manager)
    r = client.get("/parent")
    assert r.status_code == 200
    assert "about four minutes" in r.text


def test_cap_state_dims_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cap_message is set (via POST to /parent/packs), the form carries ws-form--capped."""
    active = new_run(VALID_TOKEN, PackRequest(theme="the_sleepy_sea", language="it", count=1))
    running = active.advance("running")
    manager = _FakeManager(
        raise_cap=RunCapExceeded("a story pack is already being made", active=running)
    )
    client = _make_client(monkeypatch, manager)
    # POST triggers the cap branch which re-renders packs.html with cap_message set
    r = client.post(
        "/parent/packs",
        data={"theme": "the_sleepy_sea", "language": "it", "count": "1"},
    )
    assert r.status_code == 200
    assert "ws-form--capped" in r.text  # dim+inert class applied
