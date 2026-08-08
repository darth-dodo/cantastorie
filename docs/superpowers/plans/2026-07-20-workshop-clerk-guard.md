# Workshop Behind Clerk (Scope-Driven) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the workshop's `WORKSHOP_SECRET` password gate with Clerk sign-in, and route every `/workshop` request through a server-resolved `WorkshopScope` so an operator works globally while a (future) parent is confined to their own family.

**Architecture:** Extract one Clerk-verification function (`verify_clerk_session`) that both the parent dependencies and the workshop build on. Resolve a `WorkshopScope` from the verified JWT claims (`role`, `family_token`). This slice wires the **operator** path end-to-end and threads `scope.store_token` through the handlers; a signed-in non-operator gets a 403 "coming soon" page until AI-411/AI-412 fill in the family views. Sign-in is ClerkJS mounted on a rebuilt `login.html`, and ClerkJS loads on every workshop page to keep Clerk's ~60 s `__session` JWT refreshed for HTMX polling.

**Tech Stack:** Python 3.12 / uv · FastAPI · Jinja2 + HTMX · PyJWT + cryptography (RS256) · httpx (JWKS fetch) · ClerkJS (browser, no bundler) · pytest + moto · vitest (existing).

## Global Constraints

- **No bundler / no TypeScript on the workshop frontend** — vanilla JS + Tailwind, matching the existing `src/static/js/workshop.js` and `src/templates/workshop/` pattern.
- **Feature gate = Clerk config.** If `clerk_publishable_key` OR `clerk_jwks_url` is unset, every `/workshop` route answers **404** (disabled-by-default, same shape as the old secret gate).
- **ClerkJS is load-bearing.** Clerk's `__session` JWT lives ~60 s and is refreshed only by ClerkJS in the browser; it MUST load in `base.html` on every workshop page or HTMX polling 401s a minute after sign-in.
- **Clerk session-token template must carry `role` and `family_token`** so scope is read from the verified JWT with no per-request Clerk API call. (Dashboard config; documented in setup.md, not code.)
- **Operator = `public_metadata.role == "operator"`.** Everyone else is a parent. There is no allow-list and no env flag.
- **Never touch the child player.** No Clerk, no cookies there.
- **Tests mint JWTs locally** via the existing `tests/api/clerk_jwt.py` helpers (RSA keypair + mock JWKS fetch seam) — no network, no real Clerk.
- **TDD + frequent commits.** Every task: failing test → run (fail) → minimal impl → run (pass) → commit. Run the suite with `uv run pytest`.

---

## File Structure

- `src/api/auth.py` — **modify.** Add `verify_clerk_session()` (the shared verifier); rebuild `require_parent_candidate`/`require_parent` on it. Observable behavior unchanged.
- `src/workshop/scope.py` — **create.** `WorkshopScope` value object + `resolve_scope(claims)`. Pure function of a claims dict; independently unit-tested.
- `src/config.py` — **modify.** Delete `workshop_secret`.
- `src/api/routes/workshop.py` — **modify.** Swap the secret auth for Clerk + scope; delete `POST /workshop/login`, `_session_token`, cookie logic; thread `scope.store_token`; add non-operator 403 + Clerk feature gate; pass `clerk_publishable_key` to template contexts.
- `src/templates/workshop/base.html` — **modify.** Load ClerkJS + init on every page; sign-out control.
- `src/templates/workshop/login.html` — **rewrite.** ClerkJS `<SignIn>` mount.
- `src/templates/workshop/coming_soon.html` — **create.** The non-operator 403 page.
- `src/static/js/workshop.js` — **modify.** Clerk init/sign-out wiring + `htmx:responseError` 401 → full-page reload.
- `tests/api/test_auth.py` — **modify.** Add focused `verify_clerk_session` tests (existing tests stay green).
- `tests/workshop/test_scope.py` — **create.** `resolve_scope` unit tests.
- `tests/workshop/test_routes.py` — **modify.** Harness signs a JWT + sets `__session`; add non-operator-403 and Clerk-unconfigured-404 tests.
- `.env.example`, `docs/setup.md`, `docs/architecture.md`, `docs/product.md` — **modify.** Docs.

---

## Task 1: Shared Clerk verifier (`verify_clerk_session`)

Extract the cookie-read → JWT-verify → disabled-kill-switch core out of `require_parent_candidate` into one function both the parent deps and the workshop call. `None` means "no valid identity" (missing cookie, bad/expired JWT, empty `sub`); a positively-identified-but-disabled account raises 403. The feature-guard (404) stays in each dependency.

**Files:**
- Modify: `src/api/auth.py`
- Test: `tests/api/test_auth.py`

**Interfaces:**
- Produces: `async def verify_clerk_session(request: Request, settings: Settings) -> dict[str, Any] | None` — returns the decoded JWT payload on success; `None` when unauthenticated; raises `HTTPException(403)` when the payload has `disabled: true`. Assumes `settings.clerk_jwks_url` is set (caller feature-guards).
- Consumes: existing `_get_keys`, `SESSION_COOKIE`, `require_parent_candidate`/`require_parent` (rebuilt on top).

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_auth.py` (reuse the module's existing keypair/settings fixtures and `tests/api/clerk_jwt.py` helpers; import `verify_clerk_session`, `mint_token`, `valid_payload`, `clerk_settings`, `make_mock_fetch`, `SESSION_COOKIE`):

```python
import pytest
from starlette.requests import Request
from fastapi import HTTPException

from src.api import auth as auth_mod
from src.api.auth import verify_clerk_session, SESSION_COOKIE
from tests.api.clerk_jwt import (
    generate_rsa_keypair, mint_token, valid_payload, clerk_settings, make_mock_fetch,
)


def _request_with_cookie(token: str | None) -> Request:
    headers = []
    if token is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={token}".encode()))
    scope = {"type": "http", "headers": headers}
    return Request(scope)


@pytest.mark.anyio
async def test_verify_returns_claims_for_a_valid_session(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    token = mint_token(key, valid_payload(sub="user_x", family_token="fam_1"))
    claims = await verify_clerk_session(_request_with_cookie(token), clerk_settings())
    assert claims is not None
    assert claims["sub"] == "user_x"
    assert claims["family_token"] == "fam_1"


@pytest.mark.anyio
async def test_verify_returns_none_without_a_cookie(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    assert await verify_clerk_session(_request_with_cookie(None), clerk_settings()) is None


@pytest.mark.anyio
async def test_verify_returns_none_for_a_garbage_token(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    assert await verify_clerk_session(_request_with_cookie("not.a.jwt"), clerk_settings()) is None


@pytest.mark.anyio
async def test_verify_returns_none_for_empty_sub(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    token = mint_token(key, valid_payload(sub="", family_token="fam_1"))
    assert await verify_clerk_session(_request_with_cookie(token), clerk_settings()) is None


@pytest.mark.anyio
async def test_verify_raises_403_for_disabled(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    token = mint_token(key, valid_payload(sub="user_x", disabled=True))
    with pytest.raises(HTTPException) as exc:
        await verify_clerk_session(_request_with_cookie(token), clerk_settings())
    assert exc.value.status_code == 403
```

> Note: check the top of `tests/api/test_auth.py` for how it declares the anyio backend (an `anyio_backend` fixture or `pytestmark`). Mirror that exact mechanism instead of the `@pytest.mark.anyio` shown here if it differs.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_auth.py -k verify -v`
Expected: FAIL — `ImportError: cannot import name 'verify_clerk_session'`.

- [ ] **Step 3: Implement `verify_clerk_session` and rebuild the parent deps on it**

In `src/api/auth.py`, add the function (place it just above `require_parent_candidate`, after the `_get_keys` block):

```python
async def verify_clerk_session(
    request: Request,
    settings: Settings,
) -> dict[str, Any] | None:
    """Verify the Clerk `__session` cookie and return its claims.

    Returns the decoded payload for a valid, identified session; returns None
    when there is no usable identity (missing cookie, bad/expired signature,
    empty `sub`); raises HTTPException(403) when the account is disabled.

    The feature guard (404 when Clerk is unconfigured) stays with each caller —
    this function assumes `settings.clerk_jwks_url` is set.
    """
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return None

    try:
        header = jwt.get_unverified_header(raw_token)
        kid: str = header.get("kid", "")
        keys = await _get_keys(settings.clerk_jwks_url)
        if kid not in keys:
            return None
        payload: dict[str, Any] = jwt.decode(
            raw_token,
            keys[kid],
            algorithms=["RS256"],
            issuer=settings.clerk_issuer or None,
            options={"verify_exp": True, "verify_nbf": True},
        )
    except Exception:
        return None

    # Kill switch — checked before any downstream scoping, so a disabled
    # account can never act (mirrors the pre-refactor ordering).
    if bool(payload.get("disabled", False)):
        raise HTTPException(status_code=403)

    if not str(payload.get("sub", "")):
        return None
    return payload
```

Then replace the body of `require_parent_candidate` (keep its signature and docstring) so it delegates:

```python
async def require_parent_candidate(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateContext:
    # 1. Feature guard — unset jwks_url means /parent does not exist.
    if not settings.clerk_jwks_url:
        raise HTTPException(status_code=404)
    # 2-5. Shared verification (None => unauthenticated; 403 => disabled).
    payload = await verify_clerk_session(request, settings)
    if payload is None:
        raise HTTPException(status_code=401)
    user_id = str(payload["sub"])
    raw_family = payload.get("family_token")
    family_token = raw_family if isinstance(raw_family, str) and raw_family else None
    return CandidateContext(user_id=user_id, family_token=family_token)
```

`require_parent` is unchanged (it already wraps `require_parent_candidate`).

- [ ] **Step 4: Run the auth suite to verify green**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS — the new `verify_*` tests pass AND every pre-existing `require_parent*` test still passes (404 unset, 401 missing/bad/empty-sub, 403 disabled, valid contexts).

- [ ] **Step 5: Commit**

```bash
git add src/api/auth.py tests/api/test_auth.py
git commit -m "refactor(auth): extract verify_clerk_session as the single Clerk verify path"
```

---

## Task 2: `WorkshopScope` + `resolve_scope`

A pure mapping from verified JWT claims to what the workshop grants: operator (global, publishes to the shared shelf) vs family (own runs, publishes to overlay). Store partitioning is by `store_token` — `"operator"` for operators (the existing partition), the `family_token` for parents.

**Files:**
- Create: `src/workshop/scope.py`
- Test: `tests/workshop/test_scope.py`

**Interfaces:**
- Produces:
  - `OPERATOR_STORE_TOKEN: str = "operator"`
  - `@dataclass(frozen=True) class WorkshopScope: user_id: str; is_operator: bool; store_token: str; publish_target: str` where `publish_target` is `"shared"` or `"overlay"`.
  - `def resolve_scope(claims: dict[str, Any]) -> WorkshopScope`.
- Consumes: a claims dict as returned by `verify_clerk_session` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `tests/workshop/test_scope.py`:

```python
from src.workshop.scope import WorkshopScope, resolve_scope, OPERATOR_STORE_TOKEN


def test_operator_role_gets_global_operator_scope():
    scope = resolve_scope({"sub": "user_op", "role": "operator", "family_token": "ignored"})
    assert scope == WorkshopScope(
        user_id="user_op",
        is_operator=True,
        store_token=OPERATOR_STORE_TOKEN,
        publish_target="shared",
    )


def test_non_operator_with_family_token_gets_family_scope():
    scope = resolve_scope({"sub": "user_p", "family_token": "fam_42"})
    assert scope == WorkshopScope(
        user_id="user_p",
        is_operator=False,
        store_token="fam_42",
        publish_target="overlay",
    )


def test_non_operator_without_family_token_is_a_parent_with_empty_store_token():
    scope = resolve_scope({"sub": "user_new"})
    assert scope.is_operator is False
    assert scope.store_token == ""
    assert scope.publish_target == "overlay"


def test_any_role_other_than_operator_is_a_parent():
    scope = resolve_scope({"sub": "u", "role": "admin", "family_token": "fam_1"})
    assert scope.is_operator is False
    assert scope.store_token == "fam_1"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workshop/test_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.workshop.scope'`.

- [ ] **Step 3: Implement `src/workshop/scope.py`**

```python
"""WorkshopScope: what a verified Clerk session is allowed to do in the workshop.

Resolved from JWT claims (never a per-request Clerk API call). Operators work
globally and publish to the shared shelf; everyone else is a parent, confined
to their own family_token partition and publishing to a family overlay. The
security boundary lives here — keep it a pure, exhaustively tested function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATOR_STORE_TOKEN = "operator"

OPERATOR_ROLE = "operator"


@dataclass(frozen=True)
class WorkshopScope:
    user_id: str
    is_operator: bool
    store_token: str
    publish_target: str  # "shared" for operators, "overlay" for families


def resolve_scope(claims: dict[str, Any]) -> WorkshopScope:
    user_id = str(claims.get("sub", ""))
    if claims.get("role") == OPERATOR_ROLE:
        return WorkshopScope(
            user_id=user_id,
            is_operator=True,
            store_token=OPERATOR_STORE_TOKEN,
            publish_target="shared",
        )
    family_token = claims.get("family_token")
    return WorkshopScope(
        user_id=user_id,
        is_operator=False,
        store_token=family_token if isinstance(family_token, str) and family_token else "",
        publish_target="overlay",
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/workshop/test_scope.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workshop/scope.py tests/workshop/test_scope.py
git commit -m "feat(workshop): add WorkshopScope resolved from Clerk claims"
```

---

## Task 3: Drop `workshop_secret` from config

**Files:**
- Modify: `src/config.py:58-61` (the `workshop_secret` field + its comment)
- Modify: `.env.example` (stale comment reference)
- Test: `tests/test_config.py` if one exists (see Step 1)

**Interfaces:**
- Produces: `Settings` no longer has a `workshop_secret` attribute.

- [ ] **Step 1: Write the failing test**

First check for a config test module: `ls tests | grep -i config`. If `tests/test_config.py` exists, add there; otherwise create `tests/test_config.py`:

```python
from src.config import Settings


def test_settings_has_no_workshop_secret():
    assert not hasattr(Settings(_env_file=None), "workshop_secret")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — the attribute still exists.

- [ ] **Step 3: Remove the field**

In `src/config.py`, delete these lines (currently 58-61):

```python
    # The operator face at /workshop (AI-388, ADR-005). Empty means the
    # workshop does not exist: every /workshop route answers 404. There are
    # no accounts — this one secret is the whole operator access model.
    workshop_secret: SecretStr = SecretStr("")
```

In `.env.example`, fix the now-wrong comment on line 18 — it references `WORKSHOP_SECRET above`, which no longer exists. Replace the Clerk-block comment (lines 17-19) with:

```
# Clerk auth (AI-409/AI-426). Unset means /workshop and /parent answer 404 —
# those areas do not exist. Both require CLERK_PUBLISHABLE_KEY and CLERK_JWKS_URL.
# CLERK_JWKS_URL is typically https://<your-frontend-api>.clerk.accounts.dev/.well-known/jwks.json
```

- [ ] **Step 4: Run to verify pass (and nothing else imports the field)**

Run: `uv run pytest tests/test_config.py -v && grep -rn "workshop_secret" src/`
Expected: config test PASSES; the `grep` prints nothing (Task 4 removes the `src/api/routes/workshop.py` uses; if you are doing Task 3 first, expect grep to still show workshop.py — that's fine, Task 4 clears it. Do NOT run the full suite green here; `test_routes.py` is red until Task 4.)

- [ ] **Step 5: Commit**

```bash
git add src/config.py .env.example tests/test_config.py
git commit -m "refactor(config): remove workshop_secret in favor of Clerk gate"
```

---

## Task 4: Route the workshop through Clerk + scope

The core task. Swap secret-cookie auth for Clerk verification + scope resolution; delete the login route and cookie machinery; feature-gate on Clerk config; thread `scope.store_token` through store calls; 403 non-operators; pass `clerk_publishable_key` to every rendered context.

**Files:**
- Modify: `src/api/routes/workshop.py`
- Create: `src/templates/workshop/coming_soon.html`
- Test: `tests/workshop/test_routes.py`

**Interfaces:**
- Consumes: `verify_clerk_session` (Task 1), `resolve_scope`/`WorkshopScope`/`OPERATOR_STORE_TOKEN` (Task 2), `settings.clerk_publishable_key`, `settings.clerk_jwks_url`.
- Produces: the same 12 routes at `/workshop`, now Clerk-gated and scope-aware.

- [ ] **Step 1: Rewrite the test harness and auth expectations (failing)**

In `tests/workshop/test_routes.py`, replace the secret-based setup. Remove `import hashlib`, the module-level `SECRET` constant, and the `_settings`/`_Harness.login` secret machinery. Add JWT helpers and a Clerk-configured settings + cookie-signing harness:

```python
from tests.api.clerk_jwt import (
    generate_rsa_keypair, mint_token, clerk_settings, make_mock_fetch,
)
from src.api import auth as auth_mod
from src.api.auth import SESSION_COOKIE

OPERATOR_CLAIMS = {"sub": "user_op", "role": "operator"}


def _settings(tmp_path: Path) -> Settings:
    s = clerk_settings()  # sets publishable_key + jwks_url + secret_key
    return s.model_copy(update={"r2_bucket": BUCKET, "content_dir": tmp_path / "content"})
```

> `clerk_settings()` builds a `Settings(_env_file=None, ...)`; `model_copy(update=...)` layers on the bucket/content dir. Confirm `Settings` allows this (it's a pydantic BaseSettings model — `model_copy` works). If validation rejects it, instead call `Settings(_env_file=None, clerk_publishable_key=SecretStr("pk_test_xxx"), clerk_secret_key=SecretStr("sk_test_xxx"), clerk_jwks_url="https://test.clerk.test/.well-known/jwks.json", r2_bucket=BUCKET, content_dir=tmp_path / "content")` directly.

Update `_Harness.__init__` to install the mock JWKS fetch and sign a cookie:

```python
class _Harness:
    def __init__(self, tmp_path: Path, s3: S3Client, *, configured: bool = True,
                 claims: dict | None = None) -> None:
        self.key = generate_rsa_keypair()
        # Route auth.py's JWKS fetch at our local key; reset the module cache.
        auth_mod._fetch_jwks = make_mock_fetch(self.key)
        auth_mod._jwks_state.keys = None
        auth_mod._jwks_state.fetched_at = 0.0

        self.settings = _settings(tmp_path) if configured else Settings(
            _env_file=None, r2_bucket=BUCKET, content_dir=tmp_path / "content"
        )
        self.store = RunStore(self.settings, client=s3)
        self.s3 = s3
        self.published: list[str] = []

        def fake_generate(request: PackRequest, settings: Settings) -> list[str]:
            story_id = f"{request.theme}-{request.language}-fake0001"
            _stage_fake_story(settings, s3, story_id)
            return [f"pending/staged/{story_id}"]

        self.manager = RunManager(self.store, self.settings, generate_pack=fake_generate)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_run_manager] = lambda: self.manager
        app.dependency_overrides[get_publisher] = lambda: self.published.append
        self.client = TestClient(app, base_url="https://testserver")

    def sign_in(self, claims: dict | None = None) -> None:
        payload = {**(claims or OPERATOR_CLAIMS)}
        payload.setdefault("iat", now()); payload.setdefault("nbf", now())
        payload.setdefault("exp", now() + 3600)
        token = mint_token(self.key, payload)
        self.client.cookies.set(SESSION_COOKIE, token, domain="testserver")
```

(Import `now` from `tests.api.clerk_jwt`.)

Then rewrite the auth-focused tests. Replace the four secret/cookie tests (`test_unauthenticated_workshop_shows_the_login_form_and_no_runs`, `test_..._incorrect_secret`, `test_login_sets_a_session...`, `test_the_session_cookie_is_not_the_secret_itself`, `test_login_sets_a_secure_expiring_workshop_scoped_cookie`) with:

```python
def test_workshop_does_not_exist_without_clerk_configured(tmp_path, s3):
    harness = _Harness(tmp_path, s3, configured=False)
    assert harness.client.get("/workshop").status_code == 404


def test_unauthenticated_workshop_shows_the_sign_in_page(tmp_path, s3):
    harness = _Harness(tmp_path, s3)
    page = harness.client.get("/workshop")
    assert page.status_code == 200
    assert 'id="clerk-signin"' in page.text
    assert "workshop/login" not in page.text  # no secret form anymore


def test_operator_session_sees_the_dashboard(tmp_path, s3):
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    page = harness.client.get("/workshop")
    assert page.status_code == 200
    assert 'id="clerk-signin"' not in page.text  # not the sign-in page


def test_signed_in_non_operator_gets_coming_soon_403(tmp_path, s3):
    harness = _Harness(tmp_path, s3)
    harness.sign_in({"sub": "user_parent", "family_token": "fam_1"})
    page = harness.client.get("/workshop")
    assert page.status_code == 403
    assert "coming soon" in page.text.lower()
```

Finally, replace every `harness.login()` call elsewhere in the file with `harness.sign_in()` (operator by default), and any harness constructed with a `secret=` kwarg with the plain constructor.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workshop/test_routes.py -v`
Expected: FAIL — routes still reference `settings.workshop_secret` / `_authed`; imports of `sign_in`/coming-soon fail.

- [ ] **Step 3: Rewrite `src/api/routes/workshop.py` auth core**

Replace the imports/constants/helpers region (current lines 16-97) so it uses Clerk + scope. Concretely:

Remove `import hashlib`, `import secrets`, `SESSION_COOKIE = "workshop_session"`, `OPERATOR_TOKEN = "operator"`, `_session_token`, `_authed`, and the old `_require_workshop`. Add:

```python
from src.api.auth import verify_clerk_session
from src.workshop.scope import WorkshopScope, resolve_scope

# ... keep TEMPLATES_DIR, LIVE_STATES, router, templates, Publisher, managers ...


def _require_workshop(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    """404 unless Clerk is configured — the workshop's feature gate."""
    if not settings.clerk_publishable_key.get_secret_value() or not settings.clerk_jwks_url:
        raise HTTPException(status_code=404)
    return settings


WorkshopSettings = Annotated[Settings, Depends(_require_workshop)]
Manager = Annotated[RunManager, Depends(get_run_manager)]


def _base_ctx(settings: Settings, **extra: object) -> dict[str, object]:
    """Every workshop template needs the Clerk publishable key (ClerkJS init)."""
    return {"clerk_publishable_key": settings.clerk_publishable_key.get_secret_value(), **extra}


async def _scope(request: Request, settings: Settings) -> WorkshopScope | None:
    """Resolve the caller's WorkshopScope, or None if unauthenticated.

    Raises 403 (via verify_clerk_session) for a disabled account.
    """
    claims = await verify_clerk_session(request, settings)
    if claims is None:
        return None
    return resolve_scope(claims)


def _sign_in_page(request: Request, settings: Settings, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "workshop/login.html", _base_ctx(settings), status_code=status_code
    )


def _coming_soon(request: Request, settings: Settings) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "workshop/coming_soon.html", _base_ctx(settings), status_code=403
    )
```

Keep `_to_login()` as-is (a 303 redirect to `/workshop`). Change `_record_or_404`, `_story_record_or_404` where they hard-code `OPERATOR_TOKEN`: `_record_or_404` and `staged_story` should load by `scope.store_token`. Update their signatures:

```python
def _record_or_404(manager: RunManager, scope: WorkshopScope, run_id: str) -> RunRecord:
    record = manager.store.load(scope.store_token, run_id)
    if record is None:
        raise HTTPException(status_code=404)
    return record
```

- [ ] **Step 4: Update each handler's guard (12 routes)**

Apply this uniform transformation. For **full-page GET** routes (`dashboard`, `run_page`, `staged_story`): resolve scope, render sign-in if `None`, coming-soon if not operator. For example `dashboard`:

```python
@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, settings: WorkshopSettings, manager: Manager) -> HTMLResponse:
    scope = await _scope(request, settings)
    if scope is None:
        return _sign_in_page(request, settings)
    if not scope.is_operator:
        return _coming_soon(request, settings)
    manager.reap_stale()
    runs = sorted(manager.store.list_runs(), key=lambda r: r.created_at, reverse=True)
    # ... unchanged body ...
    return templates.TemplateResponse(
        request, "workshop/dashboard.html",
        _base_ctx(settings, runs=runs, run_extras=run_extras,
                  themes=get_args(Theme), languages=get_args(Language), live=LIVE_STATES),
    )
```

`run_page` and `staged_story`: same three-line guard prelude; wrap their existing context dicts in `_base_ctx(settings, ...)`; and in `staged_story` replace `manager.store.load(OPERATOR_TOKEN, run_id)` with `manager.store.load(scope.store_token, run_id)` and `_record_or_404(manager, run_id)` calls with `_record_or_404(manager, scope, run_id)`.

For **HTMX/asset** routes that currently `raise HTTPException(404)` on unauth (`run_progress`, `staged_asset`): replace the `if not _authed(...)` with:

```python
    scope = await _scope(request, settings)
    if scope is None or not scope.is_operator:
        raise HTTPException(status_code=404)
```

(and pass `scope` into `_record_or_404` in `run_progress`).

For **mutation** routes that currently `_to_login()` on unauth (`start_run`, `approve_run`, `reject_run`, `run_again`, `delete_run`, `delete_staged_story_route`): replace the `if not _authed(...)` with:

```python
    scope = await _scope(request, settings)
    if scope is None:
        return _to_login()
    if not scope.is_operator:
        raise HTTPException(status_code=403)
```

Then thread `scope.store_token` through their store calls: `manager.submit(scope.store_token, ...)`, `manager.store.delete(scope.store_token, run_id)`, and every `_record_or_404(manager, run_id)` → `_record_or_404(manager, scope, run_id)`. (`_story_record_or_404` scans all runs via `list_runs()` and needs no token; leave it, but for an operator that is correct — revisit for family scoping in AI-412.)

Delete the entire `@router.post("/login")` handler.

- [ ] **Step 5: Create the coming-soon template**

`src/templates/workshop/coming_soon.html`:

```html
{% extends "workshop/base.html" %}
{% block title %}Coming soon{% endblock %}
{% block content %}
<div class="ws-login-wrap">
  <div class="ws-wordmark-stack">
    <div class="ws-wordmark">Your workshop</div>
    <div class="ws-tagline">coming soon</div>
  </div>
  <div class="ws-card ws-login-card">
    <p>You're signed in, but your family workshop isn't ready yet. Hang tight.</p>
    <button type="button" id="ws-signout" class="ws-pill">Sign out</button>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 6: Run the workshop route suite**

Run: `uv run pytest tests/workshop/test_routes.py -v`
Expected: PASS — the new auth tests plus every converted run/progress/publish/delete test (operator session drives them exactly as the secret login used to).

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/workshop.py src/templates/workshop/coming_soon.html tests/workshop/test_routes.py
git commit -m "feat(workshop): gate routes on Clerk sessions via WorkshopScope"
```

---

## Task 5: ClerkJS sign-in, always-on session refresh, sign-out, HTMX 401 reload

Rebuild `login.html` as a ClerkJS sign-in mount; load ClerkJS in `base.html` on every page (session refresh — the load-bearing constraint); wire sign-out; reload on HTMX 401.

**Files:**
- Modify: `src/templates/workshop/base.html`
- Rewrite: `src/templates/workshop/login.html`
- Modify: `src/static/js/workshop.js`

**Interfaces:**
- Consumes: `clerk_publishable_key` in every workshop template context (provided by `_base_ctx`, Task 4).

- [ ] **Step 1: Add the failing assertion to the route tests**

In `tests/workshop/test_routes.py`, add:

```python
def test_every_workshop_page_loads_clerk_js(tmp_path, s3):
    harness = _Harness(tmp_path, s3)
    harness.sign_in()
    page = harness.client.get("/workshop")
    assert "clerk.browser.js" in page.text
    assert 'name="clerk-publishable-key"' in page.text
    assert "pk_test_xxx" in page.text  # the key from clerk_settings()
```

Run: `uv run pytest tests/workshop/test_routes.py -k clerk_js -v`
Expected: FAIL — ClerkJS not in `base.html` yet.

- [ ] **Step 2: Load ClerkJS + publishable key in `base.html`**

In `src/templates/workshop/base.html` `<head>`, add after the existing `<meta viewport>`:

```html
    <meta name="clerk-publishable-key" content="{{ clerk_publishable_key }}" />
```

and before the closing `</head>` (after the htmx script line):

```html
    <script
      async
      crossorigin="anonymous"
      data-clerk-publishable-key="{{ clerk_publishable_key }}"
      src="https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js"
      type="text/javascript"></script>
```

Add a sign-out control to `<body>` — put it just inside `.ws-shell`, before the content block:

```html
      <button type="button" id="ws-signout" class="ws-pill ws-signout" hidden>Sign out</button>
```

- [ ] **Step 3: Rewrite `login.html` as a ClerkJS sign-in mount**

```html
{% extends "workshop/base.html" %}
{% block title %}Sign in{% endblock %}
{% block content %}
<div class="ws-login-wrap">
  <div class="ws-wordmark-stack">
    <div class="ws-wordmark">Workshop</div>
    <div class="ws-tagline">the room behind the piazza</div>
  </div>
  <div class="ws-card ws-login-card">
    <div id="clerk-signin"></div>
  </div>
  <p class="ws-mono-note">sign in with your account —<br>with Clerk unconfigured, the workshop answers 404</p>
</div>
{% endblock %}
```

- [ ] **Step 4: Clerk init, sign-in mount, sign-out, and 401 reload in `workshop.js`**

Append to `src/static/js/workshop.js`:

```javascript
// Clerk: load once, keep the __session cookie fresh (HTMX polling depends on
// it), mount <SignIn> on the sign-in page, and wire the sign-out button.
async function initClerk() {
  if (!window.Clerk) return; // script still loading; the 'load' retry below covers it
  await window.Clerk.load();
  const mount = document.getElementById("clerk-signin");
  if (mount) {
    window.Clerk.mountSignIn(mount, { afterSignInUrl: "/workshop", afterSignUpUrl: "/workshop" });
  }
  const signout = document.getElementById("ws-signout");
  if (signout && window.Clerk.user) {
    signout.hidden = false;
    signout.addEventListener("click", () => window.Clerk.signOut({ redirectUrl: "/workshop" }));
  }
}

if (window.Clerk) {
  initClerk();
} else {
  // clerk.browser.js is async; it dispatches nothing standard, so poll briefly.
  const t = setInterval(() => {
    if (window.Clerk) { clearInterval(t); initClerk(); }
  }, 50);
  setTimeout(() => clearInterval(t), 10000);
}

// A Clerk session can lapse in the gap between refreshes; an HTMX request then
// 401s. Full-page reload drops the user back onto the sign-in flow.
document.body.addEventListener("htmx:responseError", (e) => {
  if (e.detail && e.detail.xhr && e.detail.xhr.status === 401) {
    window.location.reload();
  }
});
```

- [ ] **Step 5: Run the route suite (server-rendered assertions)**

Run: `uv run pytest tests/workshop/test_routes.py -v`
Expected: PASS, including `test_every_workshop_page_loads_clerk_js`.

> The JS behaviors (Clerk mount, sign-out click, 401 reload) are not unit-tested here — the repo has no jsdom harness for the classic-script `workshop.js`, and standing one up is disproportionate to a load-and-mount glue layer. They are covered by manual verification now and belong to a Clerk-test-mode Playwright pass later (AI-414 territory). This is a deliberate, disclosed gap, not a skipped test.

- [ ] **Step 6: Commit**

```bash
git add src/templates/workshop/base.html src/templates/workshop/login.html src/static/js/workshop.js
git commit -m "feat(workshop): ClerkJS sign-in, session refresh, sign-out, HTMX 401 reload"
```

---

## Task 6: Docs

**Files:**
- Modify: `docs/setup.md` (§4 Clerk; the pending-bucket workshop note)
- Modify: `docs/architecture.md`
- Modify: `docs/product.md`

- [ ] **Step 1: Update `docs/setup.md`**

In §4 (Clerk), state that **both** the workshop and the parent area now authenticate through Clerk, and that the workshop needs `CLERK_PUBLISHABLE_KEY` + `CLERK_JWKS_URL` set or it answers 404. Add an **operator** subsection:

```markdown
### Operators

The workshop admits any signed-in user whose Clerk `public_metadata` contains
`{ "role": "operator" }`. Set it on your own user in the Clerk dashboard
(Users → your user → Metadata → Public). Everyone else is treated as a parent
and sees a "coming soon" page until the parent workshop views ship.

The Clerk **session-token template** must expose both claims so the server
reads them straight from the verified JWT:

    { "role": "{{user.public_metadata.role}}",
      "family_token": "{{user.public_metadata.family_token}}" }
```

Update the near-§43 pending-bucket note and any "operator face … env-var secret" wording that now contradicts Clerk.

- [ ] **Step 2: Update `docs/architecture.md`**

Where the parent area / workshop are described, record that the parent area is now a **scope of the `/workshop`** (one router, one template set; `WorkshopScope` decides visibility + publish target), not a separate `/parent` surface, and that the workshop is Clerk-gated (operator role). Remove/replace any "workshop = single env-var secret" statement.

- [ ] **Step 3: Update `docs/product.md` status table**

Flip the workshop's access row from the env-secret to "Clerk sign-in (operator role)", following the living-doc format (update the status table as features ship).

- [ ] **Step 4: Full suite + commit**

Run: `uv run pytest`
Expected: PASS (whole suite green).

```bash
git add docs/setup.md docs/architecture.md docs/product.md
git commit -m "docs: workshop authenticates via Clerk; parent area is a workshop scope"
```

---

## Self-Review

**Spec coverage:**
- Config gate → Clerk (§1) → Task 3 + Task 4 `_require_workshop`. ✓
- Shared `verify_clerk_session`, parent deps rebuilt (§2) → Task 1. ✓
- `WorkshopScope` resolver + unit test (§2, §5) → Task 2. ✓
- Delete secret login/cookie/config (§1, §2) → Task 3 + Task 4. ✓
- Non-operator 403 "coming soon" (§ Non-operator access, §4) → Task 4. ✓
- ClerkJS sign-in page (§3) → Task 5. ✓
- ClerkJS on every page / session refresh (§3, load-bearing) → Task 5 (`base.html`) + route test. ✓
- Sign-out button (§3) → Task 5. ✓
- HTMX 401 → reload (§4) → Task 5. ✓
- Session-token template carries role + family_token (§1, §6) → Task 6. ✓
- Tests: operator loads, unauth→sign-in, unauth mutation, non-operator 403, expired→401, Clerk-unconfigured→404, scope unit test, auth stays green (§5) → Tasks 1, 2, 4 (expired-token: covered by `verify` returning None on `jwt.decode` `verify_exp` failure → sign-in page; add an explicit expired-cookie route test in Task 4 Step 1 if you want it named — `sign_in({"sub":"user_op","role":"operator","exp": now()-10})` then assert `/workshop` renders the sign-in page). ✓
- Docs: setup, architecture, product (§6) → Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code; commands have expected output. ✓

**Type consistency:** `verify_clerk_session(request, settings) -> dict|None` used identically in Task 1 (auth) and Task 4 (`_scope`). `WorkshopScope(user_id, is_operator, store_token, publish_target)` and `resolve_scope` names match across Tasks 2 and 4. `_record_or_404(manager, scope, run_id)` signature updated consistently where called. `_base_ctx`/`_sign_in_page`/`_coming_soon`/`_scope` defined in Task 4 Step 3 and used in Steps 4-5. ✓

**One flagged verification for the implementer:** confirm the anyio test-invocation style at the top of `tests/api/test_auth.py` and mirror it (Task 1 Step 1 note), and confirm `Settings.model_copy(update=...)` is accepted (Task 4 Step 1 note) — both have inline fallbacks.
