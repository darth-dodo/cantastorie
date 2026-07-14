"""Behavior specs for the Clerk identity layer (AI-409).

require_parent reads a Clerk session JWT from the __session cookie, verifies
it via JWKS (TTL-cached, stale-if-error), and returns a ParentContext.  No
network, no Clerk SDK, no real sleeps — all crypto is local.

Pattern mirrors tests/pipeline/test_providers.py: we own the seam (the httpx
fetch function) and swap it out per test via monkeypatch.

Note: no `from __future__ import annotations` here — FastAPI resolves route
handler annotations at decoration time and PEP 563 lazy strings break that.
"""

import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import src.api.auth as auth_module
from src.api.auth import SESSION_COOKIE, ParentContext, _jwks_state, require_parent
from src.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Test-keypair helpers
# ---------------------------------------------------------------------------

TEST_KID = "test-key-001"


def _generate_rsa_keypair() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _private_key_pem(private_key: RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _jwks_document(private_key: RSAPrivateKey, kid: str = TEST_KID) -> dict[str, Any]:
    """Build a JWKS document from an RSA private key (public portion only)."""
    public_key = private_key.public_key()
    # Serialize via jwt.algorithms.RSAAlgorithm for consistency with how
    # auth.py parses keys — this round-trips through the same JWK format.
    pub_jwk_str: str = jwt.algorithms.RSAAlgorithm.to_jwk(public_key)  # type: ignore[attr-defined]
    pub_jwk: dict[str, Any] = json.loads(pub_jwk_str)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return {"keys": [pub_jwk]}


def _mint_token(
    private_key: RSAPrivateKey,
    payload: dict[str, Any],
    kid: str = TEST_KID,
) -> str:
    """Encode a JWT signed with the given private key."""
    return jwt.encode(
        payload,
        _private_key_pem(private_key),
        algorithm="RS256",
        headers={"kid": kid},
    )


# ---------------------------------------------------------------------------
# Throwaway FastAPI app used across all tests
# ---------------------------------------------------------------------------


def _make_app(settings: Settings) -> FastAPI:
    """Create a minimal FastAPI app with require_parent wired to one route."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/me")
    async def me(ctx: Annotated[ParentContext, Depends(require_parent)]) -> dict[str, str]:
        return {"user_id": ctx.user_id, "family_token": ctx.family_token}

    return app


def _clerk_settings(
    jwks_url: str = "https://test.clerk.test/.well-known/jwks.json",
    clerk_issuer: str = "",
) -> Settings:
    return Settings(
        _env_file=None,
        clerk_publishable_key=SecretStr("pk_test_xxx"),
        clerk_secret_key=SecretStr("sk_test_xxx"),
        clerk_jwks_url=jwks_url,
        clerk_issuer=clerk_issuer,
    )


# ---------------------------------------------------------------------------
# Seam: injectable fetch so tests never touch the network
# ---------------------------------------------------------------------------


def _make_mock_fetch(
    private_key: RSAPrivateKey,
    kid: str = TEST_KID,
    *,
    fail: bool = False,
) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """Return an async fetch callable that serves a local JWKS (or raises on fail).

    Async to match the awaited seam in auth.py (_fetch_jwks, AI-419).
    """
    jwks = _jwks_document(private_key, kid)

    async def _fetch(url: str) -> dict[str, Any]:
        if fail:
            raise httpx.ConnectError("simulated network failure")
        return jwks

    return _fetch


# ---------------------------------------------------------------------------
# Standard payload helper
# ---------------------------------------------------------------------------


def _now() -> int:
    return int(time.time())


def _valid_payload(
    sub: str = "user_2abc",
    family_token: str = "fam_tok_001",
    *,
    disabled: bool = False,
    include_family_token: bool = True,
    iss: str = "",
) -> dict[str, Any]:
    n = _now()
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": n,
        "nbf": n,
        "exp": n + 3600,
    }
    if include_family_token:
        payload["family_token"] = family_token
    if disabled:
        payload["disabled"] = True
    if iss:
        payload["iss"] = iss
    return payload


# ---------------------------------------------------------------------------
# Helper: reset the module-level JWKS cache between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_jwks_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the JWKS cache before each test for isolation."""
    monkeypatch.setattr(_jwks_state, "keys", None)
    monkeypatch.setattr(_jwks_state, "fetched_at", 0.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_jwks_is_async_to_avoid_blocking_the_event_loop() -> None:
    """require_parent is an async dependency; a synchronous httpx.get in the
    JWKS fetch blocks the uvicorn event loop on cold start / TTL expiry
    (AI-419). The fetch seam must be a coroutine function so it can be awaited.
    """
    assert inspect.iscoroutinefunction(auth_module._fetch_jwks)


def test_valid_token_returns_parent_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a valid Clerk session JWT with family_token,
    When require_parent processes the request,
    Then it returns a ParentContext with the correct user_id and family_token.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload(sub="user_abc", family_token="fam_xyz"))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "user_abc"
    assert data["family_token"] == "fam_xyz"


def test_expired_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a JWT whose exp is in the past,
    When require_parent processes the request,
    Then it raises 401 — the token is expired.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    n = _now()
    expired_payload = {
        "sub": "user_abc",
        "family_token": "fam_xyz",
        "iat": n - 7200,
        "nbf": n - 7200,
        "exp": n - 3600,  # expired 1 hour ago
    }
    token = _mint_token(private_key, expired_payload)

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_bad_signature_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a JWT signed by a different RSA key not present in the JWKS,
    When require_parent processes the request,
    Then it raises 401 — signature does not verify.
    """
    jwks_key = _generate_rsa_keypair()
    signing_key = _generate_rsa_keypair()  # different key — not in JWKS

    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(jwks_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(signing_key, _valid_payload())

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_missing_family_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a valid JWT that omits the family_token claim,
    When require_parent processes the request,
    Then it raises 401 — the parent is not yet provisioned.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload(include_family_token=False))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_disabled_true_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a JWT with disabled: true in the claims (kill switch),
    When require_parent processes the request,
    Then it raises 403 — the parent account is deactivated.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload(disabled=True))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 403


def test_unset_clerk_config_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given clerk_jwks_url is empty (feature not configured),
    When require_parent processes the request,
    Then it raises 404 — the parent area does not exist.
    """
    # No monkeypatch on _fetch_jwks needed — the 404 fires before any fetch.
    settings = Settings(_env_file=None, clerk_jwks_url="")
    app = _make_app(settings)

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: "any.token.here"})

    assert resp.status_code == 404


def test_missing_session_cookie_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a request with no __session cookie,
    When require_parent processes the request,
    Then it raises 401 — unauthenticated.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)

    with TestClient(app) as client:
        resp = client.get("/me")  # no cookie set

    assert resp.status_code == 401


def test_jwks_cache_stale_if_error_still_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given the JWKS cache was primed successfully,
    When the fetch fails AND the TTL has expired,
    Then a valid token still verifies (stale keys are served rather than erroring).
    """
    private_key = _generate_rsa_keypair()
    call_count = 0

    async def fetch_that_fails_second_time(url: str) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _jwks_document(private_key)
        raise httpx.ConnectError("simulated fetch failure")

    monkeypatch.setattr(auth_module, "_fetch_jwks", fetch_that_fails_second_time)

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload())

    with TestClient(app) as client:
        # First request: primes the cache (fetch_count=1, succeeds).
        resp1 = client.get("/me", cookies={SESSION_COOKIE: token})
        assert resp1.status_code == 200

        # Advance simulated time past TTL so the cache appears stale.
        monkeypatch.setattr(
            _jwks_state,
            "fetched_at",
            _jwks_state.fetched_at - auth_module.JWKS_TTL_SECONDS - 1,
        )

        # Second request: fetch fails, but stale keys are served.
        resp2 = client.get("/me", cookies={SESSION_COOKIE: token})
        assert resp2.status_code == 200

    assert call_count == 2  # both attempts were made


def test_unknown_kid_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a token whose header kid is not present in the served JWKS,
    When require_parent processes the request,
    Then it raises 401 — the kid-not-found guard fires before signature check.

    Regression guard: deleting the `if kid not in keys` branch in auth.py must
    cause this test to fail (or raise a key-error), proving the guard is tested.
    """
    private_key = _generate_rsa_keypair()
    # JWKS serves only TEST_KID; token claims a completely different kid.
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key, kid=TEST_KID))

    settings = _clerk_settings()
    app = _make_app(settings)
    # Mint an otherwise-valid token but stamp a kid that is absent from the JWKS.
    token = _mint_token(private_key, _valid_payload(), kid="wrong-key-999")

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_empty_sub_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a validly-signed token with family_token present but sub empty/absent,
    When require_parent processes the request,
    Then it raises 401 — an identity without a subject cannot be scoped.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    # sub="" is the edge case: present but empty string.
    token = _mint_token(private_key, _valid_payload(sub=""))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_jwks_cache_no_prior_fetch_on_failure_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given there is no prior cached key set,
    When the JWKS fetch fails,
    Then a 401 is returned — there is nothing to fall back to.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key, fail=True))

    settings = _clerk_settings()
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload())

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_issuer_mismatch_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given clerk_issuer is set and the token's iss claim does not match,
    When require_parent processes the request,
    Then it raises 401 — the issuer check fails.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings(clerk_issuer="https://expected.clerk.accounts.dev")
    app = _make_app(settings)
    # iss in the token points at a different Clerk instance.
    token = _mint_token(private_key, _valid_payload(iss="https://other.clerk.accounts.dev"))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401


def test_issuer_match_returns_parent_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given clerk_issuer is set and the token's iss claim matches exactly,
    When require_parent processes the request,
    Then it returns a ParentContext — issuer is valid.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    issuer = "https://expected.clerk.accounts.dev"
    settings = _clerk_settings(clerk_issuer=issuer)
    app = _make_app(settings)
    token = _mint_token(private_key, _valid_payload(sub="user_iss_ok", iss=issuer))

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "user_iss_ok"


def test_non_string_family_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a validly-signed token whose family_token claim is a non-string (e.g. integer),
    When require_parent processes the request,
    Then it raises 401 — isinstance(family_token, str) guard fires.
    """
    private_key = _generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", _make_mock_fetch(private_key))

    settings = _clerk_settings()
    app = _make_app(settings)
    n = _now()
    payload: dict[str, Any] = {
        "sub": "user_2abc",
        "iat": n,
        "nbf": n,
        "exp": n + 3600,
        "family_token": 123,  # integer, not a string
    }
    token = _mint_token(private_key, payload)

    with TestClient(app) as client:
        resp = client.get("/me", cookies={SESSION_COOKIE: token})

    assert resp.status_code == 401
