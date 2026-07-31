"""/parent page tests (AI-411): sign-in rendering, feature guard, identity dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_module
from src.api.auth import SESSION_COOKIE
from src.api.routes.parent import router as parent_router
from src.config import Settings, get_settings
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

VALID_TOKEN = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module._jwks_state, "keys", None)
    monkeypatch.setattr(auth_module._jwks_state, "fetched_at", 0.0)


def _make_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.include_router(parent_router)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def _signed_in_client(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    **payload_kwargs: Any,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    token = mint_token(private_key, valid_payload(**payload_kwargs))
    client = TestClient(_make_app(settings))
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_anonymous_gets_sign_in_page_with_clerk_script() -> None:
    # issuer set explicitly: the route derives the FAPI host from it, and
    # clerk_settings() defaults clerk_issuer to "" (pk fallback would decode
    # the dummy key's suffix into garbage).
    client = TestClient(_make_app(clerk_settings(clerk_issuer="https://test.clerk.test")))
    response = client.get("/parent")
    assert response.status_code == 200
    assert "clerk.browser.js" in response.text
    assert 'data-clerk-publishable-key="pk_test_xxx"' in response.text
    # FAPI host derived from clerk_issuer
    assert "test.clerk.test/npm/@clerk/clerk-js@6" in response.text


def test_unset_clerk_config_404s_the_page() -> None:
    client = TestClient(_make_app(clerk_settings(jwks_url="")))
    assert client.get("/parent").status_code == 404


def test_signed_in_unprovisioned_gets_onboarding_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Issuer set: rendering the sign-in page runs _fapi_host, and the dummy
    # publishable key (pk_test_xxx) is not valid base64 for the pk fallback.
    client = _signed_in_client(
        monkeypatch,
        clerk_settings(clerk_issuer="https://test.clerk.test"),
        include_family_token=False,
    )
    response = client.get("/parent")
    assert response.status_code == 200
    assert "data-parent-onboarding" in response.text  # JS will POST /parent/api/provision


def test_disabled_session_gets_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _signed_in_client(
        monkeypatch, clerk_settings(), include_family_token=False, disabled=True
    )
    assert client.get("/parent").status_code == 403


def test_provision_url_is_unchanged_after_prefix_refactor() -> None:
    app = _make_app(clerk_settings())
    assert str(app.url_path_for("provision")) == "/parent/api/provision"
