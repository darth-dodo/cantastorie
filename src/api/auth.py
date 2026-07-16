"""Clerk parent-auth identity layer (AI-409, AI-410).

Provides two FastAPI dependencies:
- `require_parent_candidate`: verifies the Clerk session JWT; tolerates a
  missing family_token (for unprovisioned first-sign-in parents, AI-410).
- `require_parent`: thin wrapper that additionally requires the family_token
  claim; identical observable contract to the AI-409 implementation.

This module is Clerk SDK-free: the only external deps are PyJWT + cryptography
for RS256, and httpx (already a project dep) for the JWKS fetch.

No routes or UI here — later steps wire require_parent into /parent routes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request

from src.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Clerk's default session cookie name.
SESSION_COOKIE = "__session"

# JWKS cache TTL. Keys are long-lived; 1 hour is well within Clerk's rotation
# cadence. Exposed as a module constant so tests can inspect and patch it.
JWKS_TTL_SECONDS: float = 3600.0

# ---------------------------------------------------------------------------
# JWKS cache state
# ---------------------------------------------------------------------------


# kid → RSA public key object (as returned by RSAAlgorithm.from_jwk).
# These are module-level so they survive across requests within a process.
# Tests reset them via monkeypatch on _jwks_cache / _jwks_cache_at.
#
# Using module-level plain variables (not wrapped in a `global` statement
# inside functions) — the cache is mutated via a helper that takes them as
# out-params through a mutable dataclass so PLW0603 never fires.
@dataclass
class _JwksState:
    keys: dict[str, Any] | None = None
    fetched_at: float = 0.0


# Single process-wide cache instance; tests patch the *fields* directly.
_jwks_state = _JwksState()

# ---------------------------------------------------------------------------
# JWKS fetch — injectable seam for tests
# ---------------------------------------------------------------------------


async def _fetch_jwks(url: str) -> dict[str, Any]:
    """Fetch and return the raw JWKS document from *url*.

    Async (httpx.AsyncClient) so it never blocks the uvicorn event loop when
    require_parent awaits it on a cold start or TTL refresh (AI-419). Uses httpx
    so tests can monkeypatch this function at the module seam (matching the
    NarrationClient transport pattern in test_providers.py). PyJWT's built-in
    PyJWKClient uses urllib under the hood and cannot be intercepted at the
    httpx seam — hence we own the fetch here.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


async def _get_keys(jwks_url: str) -> dict[str, Any]:
    """Return the cached kid→key map, refreshing if the TTL has elapsed.

    Stale-if-error: if a refresh attempt fails but we already have a cached
    key set, return the stale set rather than surfacing the error.  Only raise
    if there is no cached set at all (cold start failure leaves us no keys).
    """
    now = time.monotonic()
    cache_is_stale = (now - _jwks_state.fetched_at) >= JWKS_TTL_SECONDS

    if _jwks_state.keys is not None and not cache_is_stale:
        return _jwks_state.keys

    # Attempt to refresh.
    try:
        doc = await _fetch_jwks(jwks_url)
        keys: dict[str, Any] = {}
        for jwk_entry in doc.get("keys", []):
            kid: str = jwk_entry["kid"]
            keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(jwk_entry)
        _jwks_state.keys = keys
        _jwks_state.fetched_at = now
        return _jwks_state.keys
    except Exception:
        if _jwks_state.keys is not None:
            # Serve stale rather than failing; a transient JWKS outage must
            # not log every parent out immediately (section 6 of the design).
            return _jwks_state.keys
        raise


# ---------------------------------------------------------------------------
# ParentContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParentContext:
    """Identity returned by require_parent on a valid, provisioned session."""

    user_id: str
    family_token: str
    disabled: bool


# ---------------------------------------------------------------------------
# CandidateContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateContext:
    """Identity for a verified session that may not carry a family token yet.

    The provision endpoint (AI-410) is the only consumer: it must admit a
    first-sign-in parent whose claims have no family_token, which
    require_parent deliberately rejects.
    """

    user_id: str
    family_token: str | None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def require_parent_candidate(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateContext:
    """Verify the Clerk session but tolerate a missing family_token claim.

    Same guards as require_parent (404 feature-unset, 401 bad/missing JWT,
    403 disabled) — only the family_token requirement is relaxed. The kill
    switch is checked before provisioning state, so a disabled account can
    never mint or link a token.
    """
    # 1. Feature guard — unset jwks_url means /parent does not exist.
    if not settings.clerk_jwks_url:
        raise HTTPException(status_code=404)

    # 2. Read cookie — missing cookie is unauthenticated.
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401)

    # 3. Verify JWT signature and standard claims via PyJWT + JWKS.
    try:
        header = jwt.get_unverified_header(raw_token)
        kid: str = header.get("kid", "")
        keys = await _get_keys(settings.clerk_jwks_url)
        if kid not in keys:
            raise HTTPException(status_code=401)
        public_key = keys[kid]
        payload: dict[str, Any] = jwt.decode(
            raw_token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer or None,
            options={
                "verify_exp": True,
                "verify_nbf": True,
            },
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401) from None

    # 4. azp accepted as-is — hard-fail enforcement deferred (design spec §2).

    # 5. Kill switch — disabled=true propagates within ~60 s via Clerk's
    #    short-lived session JWT refresh cycle. Checked before any
    #    family_token logic so a disabled account cannot provision.
    if bool(payload.get("disabled", False)):
        raise HTTPException(status_code=403)

    # 6. Require a non-empty sub — an absent or empty sub cannot be scoped
    #    to a family and is treated as unauthenticated.
    user_id = str(payload.get("sub", ""))
    if not user_id:
        raise HTTPException(status_code=401)

    # 7. family_token is optional here — None means "not provisioned yet".
    raw_family = payload.get("family_token")
    family_token = raw_family if isinstance(raw_family, str) and raw_family else None
    return CandidateContext(user_id=user_id, family_token=family_token)


async def require_parent(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ParentContext:
    """FastAPI dependency: verify the Clerk session and return a ParentContext.

    Thin wrapper over require_parent_candidate that additionally requires the
    family_token claim. One deliberate edge change vs AI-409: a *disabled*
    session with *no* family_token now gets 403 (kill switch wins) rather
    than 401 — the candidate checks disabled first.
    """
    ctx = await require_parent_candidate(request, settings)
    if ctx.family_token is None:
        raise HTTPException(status_code=401)
    return ParentContext(user_id=ctx.user_id, family_token=ctx.family_token, disabled=False)
