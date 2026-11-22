"""Provision endpoint tests (AI-410, AI-497): mint, already, refusals, rate limit, failure paths."""

from __future__ import annotations

import logging
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
from src.observability import family_hash
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


@pytest.fixture(autouse=True)
def reset_provision_limit() -> None:
    parent_module.provision_limiter.clear()


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


def test_a_posted_family_token_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account can only belong to the family it started (AI-497, M6)."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    response = client.post("/parent/api/provision", json={"existing_token": VALID_TOKEN})
    assert response.status_code == 400
    assert VALID_TOKEN not in response.text
    assert spy.calls == []


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


def test_a_provisioned_account_posting_a_token_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provisioned account cannot swap its family by posting another token."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy, family_token=VALID_TOKEN)
    response = client.post("/parent/api/provision", json={"existing_token": "f" * 32})
    assert response.status_code == 400
    assert spy.calls == []


def test_any_posted_field_is_refused_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning takes no fields; nothing posted can reach an R2 key prefix."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    for bad in ["../evil", "ABCDEF0123456789ABCDEF0123456789", "short", "g" * 32]:
        response = client.post("/parent/api/provision", json={"existing_token": bad})
        assert response.status_code == 400, bad
        assert bad not in response.text
    assert spy.calls == []


def test_the_sixth_attempt_in_the_window_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    for _ in range(5):
        assert client.post("/parent/api/provision", json={}).status_code == 200
    response = client.post("/parent/api/provision", json={})
    assert response.status_code == 429
    assert len(spy.calls) == 5


def test_provisioning_logs_the_family_hash_never_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    with caplog.at_level(logging.INFO, logger="src.api.routes.parent"):
        token = client.post("/parent/api/provision", json={}).json()["family_token"]
    records = [r for r in caplog.records if getattr(r, "event", None) == "family_provisioned"]
    assert len(records) == 1
    assert records[0].family == family_hash(token)  # type: ignore[attr-defined]
    assert token not in caplog.text
    assert token not in str(records[0].__dict__)


def test_a_non_object_json_body_is_a_400_that_echoes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    secret = "a" * 32
    response = client.post(
        "/parent/api/provision",
        content=f'"{secret}"',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert secret not in response.text
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
