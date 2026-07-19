"""Provision endpoint tests (AI-410): mint, link, already, failure paths."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
import src.api.routes.parent as parent_module
from src.api.auth import SESSION_COOKIE
from src.api.clerk import ClerkAPIError
from src.api.main import create_app
from src.api.routes.parent import FAMILY_TOKEN_PATTERN
from src.api.routes.parent import router as parent_router
from src.config import Settings, get_settings
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

VALID_TOKEN = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module._jwks_state, "keys", None)
    monkeypatch.setattr(auth_module._jwks_state, "fetched_at", 0.0)


def _make_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


class _ClerkSpy:
    """Records set_family_token calls; optionally raises."""

    def __init__(self, raise_error: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_error = raise_error

    async def __call__(
        self, user_id: str, family_token: str, settings: Settings, **kwargs: Any
    ) -> None:
        if self.raise_error:
            raise ClerkAPIError("Clerk metadata update failed with status 500")
        self.calls.append((user_id, family_token))


def _signed_in_client(
    monkeypatch: pytest.MonkeyPatch,
    spy: _ClerkSpy,
    *,
    include_family_token: bool = False,
    family_token: str | None = None,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    monkeypatch.setattr(parent_module, "set_family_token", spy)
    settings = clerk_settings()
    app = _make_app(settings)
    payload_kwargs: dict[str, Any] = {"include_family_token": include_family_token}
    if family_token is not None:
        payload_kwargs = {"family_token": family_token}
    token = mint_token(private_key, valid_payload(**payload_kwargs))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_mint_path_generates_and_persists_fresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    response = client.post("/parent/api/provision", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "minted"
    assert re.fullmatch(FAMILY_TOKEN_PATTERN, body["family_token"])
    assert len(spy.calls) == 1
    assert spy.calls[0][1] == body["family_token"]


def test_link_path_adopts_posted_token_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    response = client.post("/parent/api/provision", json={"existing_token": VALID_TOKEN})
    assert response.status_code == 200
    assert response.json() == {"family_token": VALID_TOKEN, "action": "linked"}
    # valid_payload default sub is "user_2abc"
    assert spy.calls == [("user_2abc", VALID_TOKEN)]


def test_second_sign_in_makes_no_rest_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claims already carry a token → idempotent, zero Clerk calls."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy, family_token=VALID_TOKEN)
    response = client.post("/parent/api/provision", json={})
    assert response.status_code == 200
    assert response.json() == {"family_token": VALID_TOKEN, "action": "already"}
    assert spy.calls == []


def test_claims_token_wins_over_posted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provisioned account cannot overwrite its token by posting another."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy, family_token=VALID_TOKEN)
    other = "f" * 32
    response = client.post("/parent/api/provision", json={"existing_token": other})
    assert response.status_code == 200
    assert response.json() == {"family_token": VALID_TOKEN, "action": "already"}
    assert spy.calls == []


def test_malformed_posted_token_is_rejected_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token becomes an R2 key prefix — strict format or nothing."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    for bad in ["../evil", "ABCDEF0123456789ABCDEF0123456789", "short", "g" * 32]:
        response = client.post("/parent/api/provision", json={"existing_token": bad})
        assert response.status_code == 422, bad
    assert spy.calls == []


def test_clerk_rest_failure_returns_502_no_partial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ClerkSpy(raise_error=True)
    client = _signed_in_client(monkeypatch, spy)
    response = client.post("/parent/api/provision", json={})
    assert response.status_code == 502
    assert "family_token" not in response.json()


def test_unauthenticated_returns_401() -> None:
    app = _make_app(clerk_settings())
    response = TestClient(app).post("/parent/api/provision", json={})
    assert response.status_code == 401


def test_unset_clerk_config_returns_404() -> None:
    app = _make_app(clerk_settings(jwks_url=""))
    response = TestClient(app).post("/parent/api/provision", json={})
    assert response.status_code == 404


def test_router_is_wired_into_the_app() -> None:
    app = create_app()
    # url_path_for resolves across all included routers; raises NoMatchFound
    # if the route is absent — a missing wire-up is an immediate test failure.
    path = app.url_path_for("provision")
    assert str(path) == "/parent/api/provision"
