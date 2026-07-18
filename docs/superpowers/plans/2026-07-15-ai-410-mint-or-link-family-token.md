# Mint-or-Link Family Token (AI-410) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a parent's first sign-in, provision their family token — adopt a posted existing token ("link") or mint a fresh one ("mint") — and persist it to the Clerk user's `public_metadata` via one REST call; document the Clerk session-claim template and full Clerk setup.

**Architecture:** Step 3 of 7 of the Clerk parent-auth design (`docs/superpowers/specs/2026-07-12-clerk-parent-auth-family-tenancy-design.md`). Builds on the AI-409 identity layer (`src/api/auth.py`). A new `require_parent_candidate` dependency verifies the session but tolerates a missing `family_token` claim; a new `POST /parent/api/provision` endpoint runs the mint-or-link decision; a single new module (`src/api/clerk.py`) is the **only** place in the codebase that calls Clerk's REST API.

**Tech Stack:** FastAPI, PyJWT (existing), httpx (existing — no Clerk SDK), pytest with the transport-seam mock pattern from `tests/api/test_auth.py`.

## Global Constraints

- **No Clerk SDK** — plain httpx only (spec §2; issue scope). `src/api/clerk.py` is the only module that may call Clerk's REST API.
- **Family token format (canonical, defined here):** 32 lowercase hex chars, minted via `secrets.token_hex(16)`, validated with `^[0-9a-f]{32}$`. Strict validation is a security boundary: the token later becomes an R2 key prefix (`pending/{family_token}/…`), so arbitrary posted strings must never be adopted. *Note:* the Linear issue says "match the existing client-side family-token format (see `src/static/js/storage.js` `family` store)" — that store **does not exist yet** (it arrives in design step 6, connect-this-device); nothing in `src/static/js/` generates family tokens today (verified 2026-07-15). This plan's format is therefore the canonical definition both sides follow; the client never mints, it only stores what the server hands it.
- **Verified Clerk facts (Context7, clerk/clerk-docs, 2026-07-15):**
  - Session-token custom claims are configured in Dashboard → **Sessions** → *Customize session token*, syntax `"family_token": "{{user.public_metadata.family_token}}"`. Individual fields (not the whole `public_metadata` object) recommended — 1.2 KB session-token size limit.
  - Metadata write: `PATCH https://api.clerk.com/v1/users/{user_id}/metadata`, `Authorization: Bearer <secret key>`, body `{"public_metadata": {…}}`, **deep-merge** semantics. Since Clerk's 2026-05-12 API change, `PATCH /v1/users/{user_id}` no longer accepts `public_metadata` — `/metadata` is the only correct endpoint.
  - Bot sign-up protection: Dashboard → **Attack protection** → *Bot sign-up protection* toggle.
- **`require_parent` observable behavior must not regress** — all existing tests in `tests/api/test_auth.py` stay green **unmodified except for import paths** (Task 1 moves helpers). One deliberate edge-ordering change is allowed and documented in Task 2: a *disabled* user with *no* family token now gets 403 (kill switch wins) instead of 401; no existing test covers that combination.
- Run tests from the worktree root: `uv run pytest tests/api/ -v`. Lint: `uv run ruff format --check . && uv run ruff check .` (CI enforces both).
- Env vars have **no prefix** (`SettingsConfigDict` sets none): `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `CLERK_PUBLISHABLE_KEY`.
- Commit style: conventional (`feat:`, `test:`, `docs:`, `refactor:`), reference AI-410.

---

### Task 1: Extract shared Clerk JWT test helpers

The RSA/JWKS/JWT-minting helpers currently live as module-privates in `tests/api/test_auth.py`. Task 4's provision-endpoint tests need them too. Extract them into an importable module; `test_auth.py` imports instead of defining.

**Files:**
- Create: `tests/api/clerk_jwt.py`
- Modify: `tests/api/test_auth.py` (delete the moved helpers, import them)

**Interfaces:**
- Produces (used by Tasks 2 and 4's tests):
  - `TEST_KID: str`
  - `generate_rsa_keypair() -> RSAPrivateKey`
  - `jwks_document(private_key, kid=TEST_KID) -> dict[str, Any]`
  - `mint_token(private_key, payload, kid=TEST_KID) -> str`
  - `clerk_settings(jwks_url=..., clerk_issuer=...) -> Settings` (verbatim original signature)
  - `make_mock_fetch(private_key, kid=TEST_KID) -> async fetch fn` (monkeypatch target for `src.api.auth._fetch_jwks`)
  - `now() -> int`, `valid_payload(...) -> dict[str, Any]`

- [ ] **Step 1: Create `tests/api/clerk_jwt.py`**

Move these helpers **verbatim** from `tests/api/test_auth.py` (read that file first — copy the current bodies exactly, do not rewrite them): `TEST_KID`, `_generate_rsa_keypair`, `_private_key_pem`, `_jwks_document`, `_mint_token`, `_clerk_settings`, `_make_mock_fetch`, `_now`, `_valid_payload`. Strip the leading underscore from each name (they are now a public test API). Keep `_make_app` in `test_auth.py` — it is auth-test-specific. Module docstring:

```python
"""Shared Clerk JWT test helpers (AI-410).

Keyless JWT minting against a local test JWKS — the transport-seam pattern
from tests/api/test_auth.py (AI-409), extracted so provision-endpoint tests
can reuse it.
"""
```

- [ ] **Step 2: Rewrite imports in `tests/api/test_auth.py`**

Delete the moved helper definitions; add:

```python
from tests.api.clerk_jwt import (
    TEST_KID,
    clerk_settings,
    generate_rsa_keypair,
    jwks_document,
    make_mock_fetch,
    mint_token,
    now,
    valid_payload,
)
```

Update every call site in `test_auth.py` from `_helper(...)` to `helper(...)` (mechanical rename). Test *functions and assertions must not change*.

- [ ] **Step 3: Run the auth tests**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: all PASS, same count as before the refactor (run once before editing to record the count).

- [ ] **Step 4: Lint**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/api/clerk_jwt.py tests/api/test_auth.py
git commit -m "refactor(tests): extract shared Clerk JWT helpers for AI-410 provision tests"
```

---

### Task 2: `require_parent_candidate` — verified session, token optional

`require_parent` 401s when `family_token` is missing, but the provision endpoint must serve exactly that not-yet-provisioned user. Refactor `src/api/auth.py`: extract the session-verification core, add `CandidateContext` + `require_parent_candidate`, re-express `require_parent` as a thin wrapper.

**Files:**
- Modify: `src/api/auth.py`
- Test: `tests/api/test_auth.py` (append new tests)

**Interfaces:**
- Consumes: existing `_get_keys`, `SESSION_COOKIE`, `Settings`.
- Produces (used by Task 4):
  - `@dataclass(frozen=True) CandidateContext: user_id: str; family_token: str | None`
  - `async def require_parent_candidate(request, settings) -> CandidateContext` — FastAPI dependency; 404 unset config, 401 missing/bad JWT or empty `sub`, **403 disabled**; `family_token` is `None` when the claim is absent/empty/non-string.
  - `require_parent` unchanged signature, returns `ParentContext` as today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_auth.py`:

```python
# ---------------------------------------------------------------------------
# require_parent_candidate (AI-410)
# ---------------------------------------------------------------------------


def _make_candidate_app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/candidate")
    async def candidate(
        ctx: Annotated[CandidateContext, Depends(require_parent_candidate)],
    ) -> dict[str, Any]:
        return {"user_id": ctx.user_id, "family_token": ctx.family_token}

    app.dependency_overrides[get_settings] = lambda: settings
    return app


def test_candidate_without_family_token_returns_context_with_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: an unprovisioned (first sign-in) session is admitted."""
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings()
    app = _make_candidate_app(settings)
    token = mint_token(private_key, valid_payload(include_family_token=False))
    client = TestClient(app)
    response = client.get("/candidate", cookies={SESSION_COOKIE: token})
    assert response.status_code == 200
    assert response.json() == {"user_id": "user_123", "family_token": None}


def test_candidate_with_family_token_returns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings()
    app = _make_candidate_app(settings)
    token = mint_token(
        private_key, valid_payload(sub="user_abc", family_token="fam_xyz")
    )
    client = TestClient(app)
    response = client.get("/candidate", cookies={SESSION_COOKIE: token})
    assert response.status_code == 200
    assert response.json() == {"user_id": "user_abc", "family_token": "fam_xyz"}


def test_candidate_disabled_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill switch applies to unprovisioned sessions too."""
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings()
    app = _make_candidate_app(settings)
    token = mint_token(
        private_key,
        valid_payload(include_family_token=False, disabled=True),
    )
    client = TestClient(app)
    response = client.get("/candidate", cookies={SESSION_COOKIE: token})
    assert response.status_code == 403


def test_candidate_missing_cookie_returns_401() -> None:
    app = _make_candidate_app(clerk_settings())
    response = TestClient(app).get("/candidate")
    assert response.status_code == 401


def test_candidate_unset_clerk_config_returns_404() -> None:
    app = _make_candidate_app(clerk_settings(jwks_url=""))
    response = TestClient(app).get("/candidate")
    assert response.status_code == 404
```

Add the imports at the top of the file: `CandidateContext`, `require_parent_candidate` (extend the existing `from src.api.auth import ...` line). Check `valid_payload`'s signature in `tests/api/clerk_jwt.py` — it already supports `include_family_token` and `disabled` kwargs (it did as `_valid_payload` in AI-409's tests); if its default `sub` is not `"user_123"`, adjust the first test's expected `user_id` to the helper's actual default rather than changing the helper.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_auth.py -v -k candidate`
Expected: FAIL — `ImportError: cannot import name 'CandidateContext'`.

- [ ] **Step 3: Refactor `src/api/auth.py`**

Below the `ParentContext` dataclass, add:

```python
@dataclass(frozen=True)
class CandidateContext:
    """Identity for a verified session that may not carry a family token yet.

    The provision endpoint (AI-410) is the only consumer: it must admit a
    first-sign-in parent whose claims have no family_token, which
    require_parent deliberately rejects.
    """

    user_id: str
    family_token: str | None
```

Replace the body of `require_parent` and add the candidate dependency (the verification core moves into the candidate; `require_parent` wraps it):

```python
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
    return ParentContext(
        user_id=ctx.user_id, family_token=ctx.family_token, disabled=False
    )
```

Delete the now-duplicated verification body from the old `require_parent` (the module keeps exactly one copy of the JWT-verification logic, inside `require_parent_candidate`). Update the module docstring's first line to mention both dependencies (AI-409 + AI-410).

- [ ] **Step 4: Run the full auth test file**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: ALL pass — the 5 new candidate tests and every pre-existing test (401 for missing family_token, 403 disabled, 404 unset config, expired/bad-signature 401s, stale-JWKS behavior) unchanged.

- [ ] **Step 5: Lint**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/api/auth.py tests/api/test_auth.py
git commit -m "feat(auth): require_parent_candidate admits verified unprovisioned sessions (AI-410)"
```

---

### Task 3: Clerk REST client — the one place that calls Clerk's API

**Files:**
- Create: `src/api/clerk.py`
- Test: `tests/api/test_clerk_client.py`

**Interfaces:**
- Consumes: `Settings.clerk_secret_key` (SecretStr, already in config).
- Produces (used by Task 4):
  - `class ClerkAPIError(Exception)` — message includes status code, **never** the secret key.
  - `async def set_family_token(user_id: str, family_token: str, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None` — raises `ClerkAPIError` on connection failure or non-2xx.
  - `CLERK_API_BASE = "https://api.clerk.com/v1"` (module constant).

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_clerk_client.py`:

```python
"""Clerk REST client tests (AI-410) — httpx.MockTransport at the seam."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from src.api.clerk import CLERK_API_BASE, ClerkAPIError, set_family_token
from src.config import Settings


def _settings() -> Settings:
    return Settings(clerk_secret_key=SecretStr("sk_test_secret"))


def test_set_family_token_patches_metadata() -> None:
    """One PATCH to /users/{id}/metadata with the token in public_metadata."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "user_123"})

    transport = httpx.MockTransport(handler)
    import asyncio

    asyncio.run(
        set_family_token(
            "user_123", "a" * 32, _settings(), transport=transport
        )
    )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "PATCH"
    assert str(request.url) == f"{CLERK_API_BASE}/users/user_123/metadata"
    assert request.headers["Authorization"] == "Bearer sk_test_secret"
    body = json.loads(request.content)
    assert body == {"public_metadata": {"family_token": "a" * 32}}


def test_non_2xx_raises_clerk_api_error_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"errors": [{"message": "nope"}]})

    transport = httpx.MockTransport(handler)
    import asyncio

    with pytest.raises(ClerkAPIError) as excinfo:
        asyncio.run(
            set_family_token("user_123", "a" * 32, _settings(), transport=transport)
        )
    assert "422" in str(excinfo.value)
    assert "sk_test_secret" not in str(excinfo.value)


def test_connection_error_raises_clerk_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)
    import asyncio

    with pytest.raises(ClerkAPIError):
        asyncio.run(
            set_family_token("user_123", "a" * 32, _settings(), transport=transport)
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_clerk_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.clerk'`.

- [ ] **Step 3: Implement `src/api/clerk.py`**

```python
"""Clerk Backend API client (AI-410, ADR-003).

The ONLY module in the codebase that calls Clerk's REST API. One operation:
writing the family token into a user's public_metadata at provision time.
Plain httpx — no Clerk SDK (settled: the request path verifies JWTs locally
via JWKS and never talks to Clerk; this single write is the exception).

Endpoint verified against Clerk docs 2026-07-15: PATCH
/v1/users/{user_id}/metadata deep-merges public_metadata. (Clerk's
2026-05-12 API change removed metadata fields from PATCH /v1/users/{id}.)
"""

from __future__ import annotations

import httpx

from src.config import Settings

CLERK_API_BASE = "https://api.clerk.com/v1"

_TIMEOUT_SECONDS = 10.0


class ClerkAPIError(Exception):
    """Clerk REST call failed. Message carries the status, never the key."""


async def set_family_token(
    user_id: str,
    family_token: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Write family_token into the Clerk user's public_metadata.

    Deep-merge PATCH: only the family_token key is touched; other metadata
    (e.g. the disabled kill switch) is preserved. The *transport* parameter
    is the test seam (httpx.MockTransport), matching the NarrationClient
    pattern in the pipeline tests.
    """
    url = f"{CLERK_API_BASE}/users/{user_id}/metadata"
    headers = {
        "Authorization": f"Bearer {settings.clerk_secret_key.get_secret_value()}",
    }
    payload = {"public_metadata": {"family_token": family_token}}
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=_TIMEOUT_SECONDS
        ) as client:
            response = await client.patch(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise ClerkAPIError(f"Clerk metadata update failed: {type(exc).__name__}") from exc
    if response.is_error:
        raise ClerkAPIError(
            f"Clerk metadata update failed with status {response.status_code}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_clerk_client.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/api/clerk.py tests/api/test_clerk_client.py
git commit -m "feat(api): Clerk REST client — single metadata write, no SDK (AI-410)"
```

---

### Task 4: Provision endpoint — mint or link

**Files:**
- Create: `src/api/routes/parent.py`
- Modify: `src/api/main.py` (include the router — mirror the three existing `include_router` lines at `src/api/main.py:24-26`)
- Test: `tests/api/test_parent_provision.py`

**Interfaces:**
- Consumes: `require_parent_candidate`, `CandidateContext` (Task 2); `set_family_token`, `ClerkAPIError` (Task 3); test helpers (Task 1).
- Produces: `POST /parent/api/provision`, body `{"existing_token": "<32 lowercase hex>" | null}`, response `{"family_token": str, "action": "already" | "linked" | "minted"}`. Status codes: 200 success/idempotent, 401/403/404 from the dependency, 422 malformed posted token (pydantic), 502 Clerk REST failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_parent_provision.py`:

```python
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
from src.api.routes.parent import FAMILY_TOKEN_PATTERN, router as parent_router
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


class _ClerkSpy:
    """Records set_family_token calls; optionally raises."""

    def __init__(self, raise_error: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_error = raise_error

    async def __call__(
        self, user_id: str, family_token: str, settings: Settings, **kwargs: Any
    ) -> None:
        if self.raise_error:
            from src.api.clerk import ClerkAPIError

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
    response = client.post(
        "/parent/api/provision", json={"existing_token": VALID_TOKEN}
    )
    assert response.status_code == 200
    assert response.json() == {"family_token": VALID_TOKEN, "action": "linked"}
    assert spy.calls == [("user_123", VALID_TOKEN)]


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
    response = client.post(
        "/parent/api/provision", json={"existing_token": other}
    )
    assert response.status_code == 200
    assert response.json() == {"family_token": VALID_TOKEN, "action": "already"}
    assert spy.calls == []


def test_malformed_posted_token_is_rejected_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token becomes an R2 key prefix — strict format or nothing."""
    spy = _ClerkSpy()
    client = _signed_in_client(monkeypatch, spy)
    for bad in ["../evil", "ABCDEF0123456789ABCDEF0123456789", "short", "g" * 32]:  # pragma: allowlist secret
        response = client.post(
            "/parent/api/provision", json={"existing_token": bad}
        )
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
    from src.api.main import create_app

    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/parent/api/provision" in paths
```

Note for the implementer: check `valid_payload`'s actual signature in `tests/api/clerk_jwt.py` — the `family_token=...` / `include_family_token=...` kwargs and default `sub="user_123"` must match how the helper works; adapt the *test file* (not the helper) if the kwarg names differ. Check `src/api/main.py` for the app factory's actual name (`create_app` vs module-level `app`) and adapt the last test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_parent_provision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.routes.parent'`.

- [ ] **Step 3: Implement `src/api/routes/parent.py`**

```python
"""Parent-area API routes (AI-410, ADR-003).

Only the provision endpoint lives here for now; the /parent pages (sign-in,
pack request form, my-packs) arrive in the next step of the design.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import CandidateContext, require_parent_candidate
from src.api.clerk import ClerkAPIError, set_family_token
from src.config import Settings, get_settings

router = APIRouter()

# Canonical family-token format: 32 lowercase hex chars (secrets.token_hex(16)).
# Strict validation is a security boundary — the token becomes an R2 key
# prefix (pending/{family_token}/…), so posted strings must never smuggle
# path separators or casing variants into bucket keys.
FAMILY_TOKEN_PATTERN = r"^[0-9a-f]{32}$"


def mint_family_token() -> str:
    """128 bits of randomness, matching FAMILY_TOKEN_PATTERN."""
    return secrets.token_hex(16)


class ProvisionRequest(BaseModel):
    """Body posted by the onboarding page.

    existing_token is the browser's IndexedDB family token if one exists
    (same origin, so a child device's token is adoptable — the "link" path).
    """

    existing_token: str | None = Field(default=None, pattern=FAMILY_TOKEN_PATTERN)


class ProvisionResponse(BaseModel):
    family_token: str
    action: str  # "already" | "linked" | "minted"


@router.post("/parent/api/provision")
async def provision(
    body: ProvisionRequest,
    ctx: Annotated[CandidateContext, Depends(require_parent_candidate)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProvisionResponse:
    """Mint-or-link the family token at first sign-in.

    Idempotent: if the session claims already carry a token, return it and
    make no Clerk call — a provisioned account cannot overwrite its token
    (rotation is a documented manual procedure, ADR-003).
    """
    if ctx.family_token is not None:
        return ProvisionResponse(family_token=ctx.family_token, action="already")

    if body.existing_token is not None:
        family_token, action = body.existing_token, "linked"
    else:
        family_token, action = mint_family_token(), "minted"

    try:
        await set_family_token(ctx.user_id, family_token, settings)
    except ClerkAPIError:
        # No partial state: nothing was stored locally, and Clerk either
        # rejected or never received the write. The client may simply retry.
        raise HTTPException(
            status_code=502, detail="could not save the family token; try again"
        ) from None

    return ProvisionResponse(family_token=family_token, action=action)
```

Wire into `src/api/main.py`, mirroring the existing pattern exactly:

```python
from src.api.routes.parent import router as parent_router
# … alongside the existing include_router calls:
app.include_router(parent_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_parent_provision.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: everything green — no regression in workshop/player/published routes.

- [ ] **Step 6: Lint**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/parent.py src/api/main.py tests/api/test_parent_provision.py
git commit -m "feat(parent): mint-or-link family token provision endpoint (AI-410)"
```

---

### Task 5: `docs/setup.md` — Clerk section with the verified session-token template

**Files:**
- Modify: `docs/setup.md` (new section after "## 3. The Render web service"; renumber "## 4. Verify" if the doc uses strict numbering — read the file first and follow its conventions)

**Interfaces:**
- Consumes: verified Clerk facts from Global Constraints; env var names `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`, `CLERK_ISSUER` (no prefix — `src/config.py:76-81`).
- Produces: a section a fresh operator can follow from zero to working claims.

- [ ] **Step 1: Write the section**

Content to add (adapt heading number to the doc's scheme):

```markdown
## Clerk (parent sign-in, ADR-003)

The parent area authenticates through Clerk. The player never loads Clerk
JS; the server verifies session JWTs locally via JWKS — the only REST call
to Clerk in the whole codebase is the one-time family-token write at first
sign-in (`src/api/clerk.py`).

### 1. Create the application

1. [dashboard.clerk.com](https://dashboard.clerk.com) → **Create application**.
2. Sign-in options: enable **Email** with **magic link** (passwordless).
   Optionally enable **Google** OAuth.
3. Note the **Publishable key** and **Secret key** from **API keys**.
   The **Frontend API URL** on the same page gives you the other two values:
   - JWKS URL: `https://<frontend-api>/.well-known/jwks.json`
   - Issuer: `https://<frontend-api>`

### 2. Session token template (custom claims)

Dashboard → **Sessions** → **Customize session token** → Claims editor:

    {
      "family_token": "{{user.public_metadata.family_token}}",
      "disabled": "{{user.public_metadata.disabled}}"
    }

Save. Individual fields — not the whole `public_metadata` object — keep the
session token under Clerk's 1.2 KB limit. Until a user is provisioned these
claims resolve to null, which the server treats as "not provisioned yet"
(and `disabled: null` as not disabled).

### 3. Bot protection

Dashboard → **Attack protection** → enable **Bot sign-up protection**.
Sign-up now guards a wallet (pack generation costs money), so this is
required, not optional. Suspected bots get an interactive challenge; if we
later build a custom sign-up form it must include the
`<div id="clerk-captcha">` placeholder element.

### 4. Environment variables (Render → Environment)

| Variable | Value |
| -- | -- |
| `CLERK_PUBLISHABLE_KEY` | `pk_…` from API keys |
| `CLERK_SECRET_KEY` | `sk_…` from API keys |
| `CLERK_JWKS_URL` | `https://<frontend-api>/.well-known/jwks.json` |
| `CLERK_ISSUER` | `https://<frontend-api>` |

Leaving `CLERK_JWKS_URL` unset disables the whole parent surface (routes
404) — the safe default for deploys that don't want Clerk yet.

### 5. Verify

Sign in at `/parent` (once the pages land — until then, any Clerk-hosted
account page works for template testing), then decode the `__session`
cookie at jwt.io: it must carry `family_token` and `disabled` claims
(null before first provision).
```

- [ ] **Step 2: Sanity-check the walkthrough against the code**

Confirm each env var name in the table matches a `Settings` field in `src/config.py` exactly (uppercase of the field name, no prefix). Confirm the "routes 404 when unset" claim matches `require_parent_candidate`'s feature guard.

- [ ] **Step 3: Commit**

```bash
git add docs/setup.md
git commit -m "docs(setup): Clerk section — app creation, session-claim template, bot protection (AI-410)"
```

---

### Task 6: ADR-003 — record the EU-residency and free-tier findings

ADR-003's Validation list already ticks "EU data-residency posture confirmed" and "free-tier limits re-checked" (`docs/adr/ADR-003-parent-authentication-clerk.md:364-365`), but the document body still carries "*unverified*" hedges at lines ~187 and ~193. The findings were never written down. This task verifies both facts **now** and records them, making the ticks honest.

**Files:**
- Modify: `docs/adr/ADR-003-parent-authentication-clerk.md`

- [ ] **Step 1: Verify the two facts (web research)**

Use WebSearch/WebFetch against clerk.com (pricing page, docs, DPA/legal pages), as of today's date:
1. **EU data residency**: where Clerk stores user records; whether an EU region can be selected; what the DPA/SCC posture is for GDPR compliance. Record what is actually true, including if the answer is "US-hosted with SCCs, no EU pinning" — an unflattering finding recorded honestly beats a vague tick.
2. **Free-tier limits**: current MAU allowance and any relevant caps (custom session token claims availability on the free plan matters too — check it), vs. this app's expected scale (a handful of families).

- [ ] **Step 2: Amend the ADR**

- Replace the "*unverified, confirm current pricing*" hedge (line ~187) with the verified free-tier figure and date.
- Replace the "EU data residency is unverified" con (line ~193) with the verified posture and date.
- Add a short **Findings** note under the Validation section (or nearest fitting spot per the doc's structure) summarizing both results in 2-4 lines, dated.
- Do **not** re-litigate the decision: if residency turns out worse than hoped, record it and flag it in the final report — the fallback path (Option D's shape) is already documented in the ADR's risk table.

- [ ] **Step 3: Check documentation conventions**

The `documentation-conventions` skill governs ADR edits — invoke it before writing and follow its amendment format (dated amendments, no silent rewrites of accepted content).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ADR-003-parent-authentication-clerk.md
git commit -m "docs(adr): record verified Clerk EU-residency and free-tier findings in ADR-003 (AI-410)"
```

---

## Final Verification Wave

- [ ] `uv run pytest` — whole suite green.
- [ ] `uv run ruff format --check . && uv run ruff check .` — clean (CI runs both).
- [ ] `npm test` if the repo's JS tests run in CI (no JS changed; this is a regression guard only).
- [ ] Grep check — the no-SDK constraint holds: `grep -rn "clerk" pyproject.toml` shows no Clerk SDK dependency; `grep -rln "api.clerk.com" src/` shows only `src/api/clerk.py`.
- [ ] Acceptance recap against AI-410: mint path ✓ (test), link path ✓ (test), second sign-in no REST call ✓ (test), REST failure → clear error/no partial state ✓ (test), setup.md walks a fresh Clerk app to working claims ✓ (Task 5), ADR-003 residency/free-tier findings recorded ✓ (Task 6).
