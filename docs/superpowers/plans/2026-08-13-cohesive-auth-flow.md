# Cohesive Auth Flow — Phase 1: Role Dispatch & Dead-End Removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Clerk-gated flow cohesive at the routing layer — every signed-in user lands on their role's home, no door is a dead-end — and delete the false "coming soon" page.

**Architecture:** Each authed page entry route resolves the caller's role and applies one rule: *serve if this is your home, else 303-redirect to your home.* A single `home_path(is_operator)` helper is the source of truth. Because each role has exactly one serving home, redirects can't loop. This is the design's §1; the sign-in/visual/ClerkJS consolidation (§2) is deferred to a separate Phase 2 plan (see "Deferred" below) because the two surfaces run different ClerkJS major versions — a frontend migration with its own risk profile and browser-only verification.

**Tech Stack:** Python 3.12 / uv · FastAPI · Jinja2 + HTMX · PyJWT (RS256) · pytest with `tests/api/clerk_jwt.py` keyless-JWT helpers.

## Global Constraints

- **Roles (verbatim from the code):** superuser = `public_metadata.role == "operator"` → surfaced as the `role` JWT claim; home `/workshop`. Everyone else = parent; home `/parent`. A superuser gets **no** parent view in this cut — routed to the workshop, full stop.
- **Feature gate unchanged:** unset `clerk_publishable_key`/`clerk_jwks_url` → both `/workshop` and `/parent` answer **404**. Do not touch this.
- **Kill switch unchanged:** a `disabled` claim → **403**, checked in `verify_clerk_session` before any scoping.
- **No child-player change.** No Clerk anywhere near the player.
- **TDD + frequent commits.** Every task: failing test → run (fail) → minimal impl → run (pass) → commit. Run tests with `uv run pytest`.
- **The single role-name constant is `OPERATOR_ROLE` in `src/workshop/scope.py`** — import it, never re-hardcode `"operator"`.

---

## Task 1: `home_path` — the one source of truth for a role's home

**Files:**
- Create: `src/api/routes/_nav.py`
- Test: `tests/api/test_nav.py`

**Interfaces:**
- Produces: `def home_path(is_operator: bool) -> str` — returns `"/workshop"` for operators, `"/parent"` otherwise. Consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_nav.py`:

```python
from src.api.routes._nav import home_path


def test_operator_home_is_the_workshop() -> None:
    assert home_path(True) == "/workshop"


def test_non_operator_home_is_the_parent_area() -> None:
    assert home_path(False) == "/parent"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_nav.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.routes._nav'`.

- [ ] **Step 3: Implement**

Create `src/api/routes/_nav.py`:

```python
"""Shared post-sign-in navigation: where each role belongs.

The one source of truth for a role's home route. Every authed page entry
point (workshop, parent) serves its own role and 303-redirects the other
role here — so no role ever hits a dead-end. Because each role has exactly
one *serving* home, redirects can never loop.
"""

from __future__ import annotations


def home_path(is_operator: bool) -> str:
    """The route a signed-in user belongs on: operators author in the
    workshop; everyone else lives in the parent area."""
    return "/workshop" if is_operator else "/parent"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/api/test_nav.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/_nav.py tests/api/test_nav.py
git commit -m "feat(auth): add home_path — the one source of truth for a role's home (AI-430)"
```

---

## Task 2: Workshop redirects non-operators to `/parent`; delete the dead-end

Replace the `coming_soon` 403 branch in the workshop dashboard with a 303 to the parent home, and delete the page + its helper. After this, a signed-in non-operator at `/workshop` is bounced to `/parent` instead of a wall.

**Files:**
- Modify: `src/api/routes/workshop.py` (the `dashboard` route + remove `_coming_soon`)
- Delete: `src/templates/workshop/coming_soon.html`
- Test: `tests/workshop/test_routes.py`

**Interfaces:**
- Consumes: `home_path` (Task 1).
- Produces: `GET /workshop` for a signed-in non-operator returns `303` with `Location: /parent`.

- [ ] **Step 1: Rewrite the failing test**

In `tests/workshop/test_routes.py`, replace the existing `test_signed_in_non_operator_gets_coming_soon_403` with:

```python
def test_signed_in_non_operator_is_redirected_to_parent(tmp_path, s3):
    harness = _Harness(tmp_path, s3)
    harness.sign_in({"sub": "user_parent", "family_token": "fam_1"})
    resp = harness.client.get("/workshop", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/parent"


def test_the_coming_soon_template_is_gone(tmp_path, s3):
    from src.api.routes.workshop import TEMPLATES_DIR

    assert not (TEMPLATES_DIR / "workshop" / "coming_soon.html").exists()
```

(`_Harness.sign_in(claims)` mints a `__session` JWT and sets it on the test client; the default is an operator, so pass explicit non-operator claims here. `follow_redirects=False` is required to observe the 303 — httpx follows redirects by default.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/workshop/test_routes.py -k "non_operator or coming_soon" -v`
Expected: FAIL — the route still returns the 403 `coming_soon` page; the template still exists.

- [ ] **Step 3: Rewrite the dashboard branch**

In `src/api/routes/workshop.py`, add the import near the other route imports:

```python
from src.api.routes._nav import home_path
```

Change the `dashboard` signature's return annotation from `-> HTMLResponse` to `-> Response` (a redirect is not an `HTMLResponse`; `Response` is already imported from `fastapi.responses`). Replace the non-operator branch:

```python
@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, settings: WorkshopSettings, manager: Manager) -> Response:
    scope = await _scope(request, settings)
    if scope is None:
        return _sign_in_page(request, settings)
    if not scope.is_operator:
        # No dead-end: a signed-in parent belongs in the parent area.
        return RedirectResponse(home_path(scope.is_operator), status_code=303)
    # ... rest of the operator dashboard body unchanged ...
```

Then delete the `_coming_soon` helper function entirely (the `def _coming_soon(...)` block).

- [ ] **Step 4: Delete the template**

```bash
git rm src/templates/workshop/coming_soon.html
```

- [ ] **Step 5: Run the workshop route suite**

Run: `uv run pytest tests/workshop/test_routes.py -v`
Expected: PASS — the redirect + template-gone tests pass, and every existing operator-path test still passes (operator still reaches the dashboard; unauth still gets the sign-in page).

- [ ] **Step 6: Commit**

```bash
git add src/api/routes/workshop.py tests/workshop/test_routes.py
git commit -m "feat(workshop): redirect signed-in non-operators to /parent; drop coming-soon dead-end (AI-430)"
```

---

## Task 3: Parent redirects superusers to `/workshop`

Carry the operator flag on the verified candidate identity, and in `parent_home` bounce a superuser to the workshop instead of rendering a parent page for them.

**Files:**
- Modify: `src/api/auth.py` (`CandidateContext` + `require_parent_candidate`)
- Modify: `src/api/routes/parent.py` (`parent_home`)
- Test: `tests/api/test_auth.py`, `tests/api/test_parent_pages.py`

**Interfaces:**
- Consumes: `home_path` (Task 1); `OPERATOR_ROLE` from `src.workshop.scope`.
- Produces: `CandidateContext` gains `is_operator: bool` (default `False`); `GET /parent` for a signed-in operator returns `303` with `Location: /workshop`.

- [ ] **Step 1: Write the failing auth test**

In `tests/api/test_auth.py`, add (reuse the module's existing keypair/settings fixtures and `tests/api/clerk_jwt.py` helpers exactly as the neighboring `verify`/`require_parent_candidate` tests do):

```python
@pytest.mark.anyio
async def test_candidate_carries_operator_flag(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    token = mint_token(key, valid_payload(sub="user_op", role="operator"))
    ctx = await require_parent_candidate(_request_with_cookie(token), clerk_settings())
    assert ctx.is_operator is True


@pytest.mark.anyio
async def test_candidate_without_operator_role_is_not_operator(monkeypatch):
    key = generate_rsa_keypair()
    monkeypatch.setattr(auth_mod, "_fetch_jwks", make_mock_fetch(key))
    monkeypatch.setattr(auth_mod._jwks_state, "keys", None)
    monkeypatch.setattr(auth_mod._jwks_state, "fetched_at", 0.0)
    token = mint_token(key, valid_payload(sub="user_p", family_token="fam_1"))
    ctx = await require_parent_candidate(_request_with_cookie(token), clerk_settings())
    assert ctx.is_operator is False
```

> Mirror the file's existing anyio invocation style and its `_request_with_cookie` helper (or the equivalent it uses to build a `Request` with the `__session` cookie). If `valid_payload` does not accept a `role` kwarg, add the claim to the returned dict in the test — never edit the shared helper.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_auth.py -k operator_flag -v`
Expected: FAIL — `CandidateContext` has no `is_operator` attribute.

- [ ] **Step 3: Implement on the candidate**

In `src/api/auth.py`, import the role constant near the top:

```python
from src.workshop.scope import OPERATOR_ROLE
```

Add the field to `CandidateContext` (default keeps every existing constructor call valid):

```python
@dataclass(frozen=True)
class CandidateContext:
    user_id: str
    family_token: str | None
    is_operator: bool = False
```

In `require_parent_candidate`, set it from the verified payload:

```python
    user_id = str(payload["sub"])
    raw_family = payload.get("family_token")
    family_token = raw_family if isinstance(raw_family, str) and raw_family else None
    is_operator = payload.get("role") == OPERATOR_ROLE
    return CandidateContext(user_id=user_id, family_token=family_token, is_operator=is_operator)
```

- [ ] **Step 4: Run the auth suite**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS — the two new tests pass and every pre-existing auth test still passes (the new field has a default, so `require_parent` and provision are unaffected).

- [ ] **Step 5: Write the failing parent-page test**

In `tests/api/test_parent_pages.py`, add (reuse the file's `_signed_in_client` / `clerk_settings` helpers):

```python
def test_operator_is_redirected_from_parent_to_workshop(monkeypatch):
    client = _signed_in_client(monkeypatch, clerk_settings(), role="operator")
    resp = client.get("/parent", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/workshop"
```

> `_signed_in_client(monkeypatch, settings, **payload_kwargs)` mints a session token via `valid_payload(**payload_kwargs)`. If `valid_payload` doesn't take `role`, extend the test's own client helper to inject the claim — do not edit `clerk_jwt.py`.

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/api/test_parent_pages.py -k operator_is_redirected -v`
Expected: FAIL — `parent_home` renders a parent template (200) instead of redirecting.

- [ ] **Step 7: Implement the parent dispatch**

In `src/api/routes/parent.py`, add the import:

```python
from src.api.routes._nav import home_path
```

Change `parent_home`'s return annotation from `-> HTMLResponse` to `-> Response` (already imported from `fastapi`). Add the operator branch right after the unauthenticated check:

```python
    ctx = await _page_identity(request, settings)
    context: dict[str, object] = {
        "fapi_host": _fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "parent/signin.html", context)
    if ctx.is_operator:
        # Superusers author in the workshop — they have no parent view here.
        return RedirectResponse(home_path(True), status_code=303)
    if ctx.family_token is None:
        # ... existing onboarding branch unchanged ...
```

- [ ] **Step 8: Run both API suites**

Run: `uv run pytest tests/api/ -v`
Expected: PASS — operator→/workshop redirect passes; every existing parent-page test (sign-in render, onboarding, packs, provision, tenancy, Clerk-containment guard) still passes.

- [ ] **Step 9: Commit**

```bash
git add src/api/auth.py src/api/routes/parent.py tests/api/test_auth.py tests/api/test_parent_pages.py
git commit -m "feat(parent): redirect superusers to the workshop; carry is_operator on CandidateContext (AI-430)"
```

---

## Final Verification Wave

- [ ] `uv run pytest` — whole suite green.
- [ ] `make check` — ruff lint + format + mypy strict clean.
- [ ] `uv run pytest tests/api/test_parent_pages.py -k clerk_loads_nowhere -v` — the Clerk-containment guard still passes (Phase 1 deletes only `coming_soon.html`, which has no Clerk refs).
- [ ] Manual dispatch matrix check against a local Clerk dev instance (optional but recommended): operator at `/workshop` → dashboard; parent at `/workshop` → 303 `/parent`; operator at `/parent` → 303 `/workshop`; parent at `/parent` → packs/onboarding.

## Self-Review

**Spec coverage (this plan = design §1 + §3):**
- Role dispatch matrix (§1): parent→/workshop 303 (Task 2), operator→/parent 303 (Task 3), operator served at /workshop + parent served at /parent (unchanged, asserted by existing tests). ✓
- `home_path` single source of truth (§1): Task 1. ✓
- Delete `coming_soon.html` (§3): Task 2. ✓
- Role model, feature-gate 404, disabled 403 preserved (§ Global Constraints): untouched; asserted by existing suites. ✓
- No redirect loops: structural — each role's home *serves* (operator at /workshop, parent at /parent), only the other door redirects. ✓

**Deferred to Phase 2 (separate spec/plan): design §2 + §4.** The one-shared-sign-in surface, the shared base template, and the unified sign-out / session-refresh / HTMX-401 handling. Deferred because the two surfaces load **different ClerkJS major versions** (workshop `clerk-js@5` via jsdelivr + `mountSignIn`; parent `clerk-js@6` + `ui@1` via the Frontend-API host) — unifying them is a frontend migration requiring a version decision and browser-only (Playwright/manual) verification, independent of this plan's server-side routing. Phase 1 ships a fully cohesive *routing* experience on its own; the two sign-in pages simply still look different until Phase 2.

**Placeholder scan:** none — every step has real code and exact commands.

**Type consistency:** `home_path(is_operator: bool) -> str` used identically in Tasks 2 and 3; `CandidateContext.is_operator: bool` defined in Task 3 Step 3 and read in Step 7; return annotations widened to `Response` where a route now returns a redirect. ✓
