# /parent Surface (AI-411) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The parent-facing half of the workshop: a Clerk sign-in page, a pack-request form feeding the existing `RunManager`, and a my-packs list with HTMX progress polling — all strictly scoped to the session's `family_token`, with per-family run caps.

**Architecture:** Step 4 of 7 of the Clerk design (`docs/superpowers/specs/2026-07-12-clerk-parent-auth-family-tenancy-design.md`). Extends `src/api/routes/parent.py` (AI-410's provision endpoint) with server-rendered Jinja2 + HTMX pages mirroring `src/api/routes/workshop.py`'s patterns. ClerkJS loads via plain script tags on `/parent` pages **only** — never the player or workshop. Run caps live in `RunManager.submit` so every submit path is capped except the operator's.

**Tech Stack:** FastAPI + Jinja2Templates + HTMX (all existing), ClerkJS v6 drop-in via CDN script tag (no npm, no bundler), pytest with `tests/api/clerk_jwt.py` keyless-JWT helpers.

## Global Constraints

- **Clerk assets on `/parent` pages only.** No Clerk script tag, hostname, or cookie logic may appear in any template outside `src/templates/parent/` or any JS under `src/static/js/` (settled, ADR-003; a guard test in Task 5 enforces it).
- **Tenancy rule:** every R2 read/write derives its `family_token` from the verified session (`ParentContext.family_token`) — NEVER from a URL or form parameter. `pending/{family_token}/` is the tenancy boundary.
- **Verified ClerkJS drop-in (Context7, clerk/clerk-docs, 2026-07-19):** two script tags from the instance's Frontend API CDN — `https://{fapi}/npm/@clerk/ui@1/dist/ui.browser.js` and `https://{fapi}/npm/@clerk/clerk-js@6/dist/clerk.browser.js` with `data-clerk-publishable-key`, both `defer crossorigin="anonymous"`; then `await window.Clerk.load({ui: {ClerkUI: window.__internal_ClerkUICtor}})` and `Clerk.mountSignIn(el)`. Options beyond publishable-key/proxy-url/domain go to `load()`, not data-attributes.
- **Run caps:** one **active** run (state `queued` or `running`) per family, plus a daily cap `parent_daily_run_cap` (config, default **3**, UTC calendar day). `OPERATOR_TOKEN` submissions are exempt. Cap hit → friendly message including the active run's state — never a bare 4xx page.
- **Feature guard:** unset `clerk_jwks_url` → every `/parent` route 404s (already enforced by `require_parent_candidate`; page routes must preserve it).
- **Existing provision endpoint's public contract is frozen:** `POST /parent/api/provision` path and response schema must not change (tests + AI-410 acceptance depend on it).
- Run tests from the worktree root: `uv run pytest`. Lint: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .` (CI enforces both). JS untouched → `npm test` is a regression guard only.
- Commit style: conventional, reference AI-411.

## File Structure

- `src/config.py` — add `parent_daily_run_cap: int = 3`
- `src/workshop/manager.py` — `OPERATOR_TOKEN` moves here; new `RunCapExceeded`; cap enforcement in `submit`
- `src/api/routes/workshop.py` — import `OPERATOR_TOKEN` from manager (local def deleted)
- `src/api/routes/parent.py` — router gains `prefix="/parent"`; new page routes: `GET /parent`, `POST /parent/packs`, `GET /parent/packs/{run_id}/progress`
- `src/templates/parent/base.html`, `signin.html`, `packs.html` — new, mirroring `src/templates/workshop/` idioms
- `src/templates/workshop/_progress.html` — parametrized with `base_url` / `is_operator` (defaults preserve workshop behavior byte-for-byte)
- Tests: `tests/workshop/test_manager.py` (caps), `tests/api/test_parent_pages.py` (new), guard test in the same new file

---

### Task 1: Run caps in `RunManager.submit`

**Files:**
- Modify: `src/config.py` (after the Clerk block, ~line 82)
- Modify: `src/workshop/manager.py`
- Modify: `src/api/routes/workshop.py:48` (delete local `OPERATOR_TOKEN`, import from manager)
- Test: `tests/workshop/test_manager.py` (append)

**Interfaces:**
- Consumes: `RunStore.list_runs(family_token=...)`, `RunRecord.state/created_at`, `Settings`.
- Produces (Task 3 relies on): `OPERATOR_TOKEN = "operator"` importable from `src.workshop.manager`; `class RunCapExceeded(Exception)` with attributes `active: RunRecord | None` (the blocking active run, if that was the reason) and a human-readable `str(exc)`; `submit` raising it for non-operator tokens.

- [ ] **Step 1: Write the failing tests**

Append to `tests/workshop/test_manager.py` (read the file first — reuse its existing fixtures/fakes for `RunStore`/`Settings`; the code below names them `make_manager`/`make_settings` — adapt those call sites to the file's actual helper names, never the helpers themselves):

```python
# ---------------------------------------------------------------------------
# Per-family run caps (AI-411)
# ---------------------------------------------------------------------------


async def test_second_active_run_is_rejected() -> None:
    manager = make_manager()
    first = await manager.submit("a" * 32, PackRequest(theme="friendship", language="it", count=1))
    assert first.state == "queued"
    with pytest.raises(RunCapExceeded) as excinfo:
        await manager.submit("a" * 32, PackRequest(theme="friendship", language="it", count=1))
    assert excinfo.value.active is not None
    assert excinfo.value.active.id == first.id


async def test_daily_cap_rejects_fourth_submit() -> None:
    manager = make_manager()  # default parent_daily_run_cap = 3
    token = "b" * 32
    for _ in range(3):
        record = await manager.submit(token, PackRequest(theme="friendship", language="it", count=1))
        # settle it so the active-run rule doesn't fire first
        manager.store.save(record.advance("running").advance("failed", error="x"))
    with pytest.raises(RunCapExceeded) as excinfo:
        await manager.submit(token, PackRequest(theme="friendship", language="it", count=1))
    assert excinfo.value.active is None  # daily cap, not active-run


async def test_operator_is_exempt_from_caps() -> None:
    manager = make_manager()
    for _ in range(5):
        await manager.submit(OPERATOR_TOKEN, PackRequest(theme="friendship", language="it", count=1))
    # five concurrent queued operator runs, no exception


async def test_other_family_runs_do_not_count() -> None:
    manager = make_manager()
    await manager.submit("a" * 32, PackRequest(theme="friendship", language="it", count=1))
    record = await manager.submit("c" * 32, PackRequest(theme="friendship", language="it", count=1))
    assert record.state == "queued"
```

Add imports `OPERATOR_TOKEN, RunCapExceeded` to the file's `from src.workshop.manager import ...` line. If the existing theme/language literals in the file differ (check `src/pipeline/models.py` for valid `Theme`/`Language` values), use whatever literal the file's existing tests already use.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workshop/test_manager.py -v -k "cap or exempt or other_family"`
Expected: FAIL — `ImportError: cannot import name 'RunCapExceeded'`.

- [ ] **Step 3: Implement**

`src/config.py`, after the Clerk fields (keep the comment style of the file):

```python
    # Per-family run caps for the /parent surface (AI-411). One active run per
    # family is always enforced; this bounds how many runs a family may start
    # per UTC day. Operator submissions from /workshop are exempt.
    parent_daily_run_cap: int = 3
```

`src/workshop/manager.py` — near the top (after imports), add:

```python
# The operator face submits under this pseudo-family token; it is exempt from
# the per-family caps below. Moved here from routes/workshop.py (AI-411) so the
# exemption lives beside the enforcement.
OPERATOR_TOKEN = "operator"


class RunCapExceeded(Exception):
    """A non-operator family hit a run cap. `active` is the blocking run when
    the one-active-run rule fired, None when the daily cap fired."""

    def __init__(self, message: str, *, active: RunRecord | None = None) -> None:
        super().__init__(message)
        self.active = active
```

Replace `submit` with:

```python
    async def submit(self, family_token: str, request: PackRequest) -> RunRecord:
        if family_token != OPERATOR_TOKEN:
            self._enforce_caps(family_token)
        record = new_run(family_token, request)
        self._store.save(record)
        return record

    def _enforce_caps(self, family_token: str) -> None:
        runs = self._store.list_runs(family_token=family_token)
        for run in runs:
            if run.state in ("queued", "running"):
                raise RunCapExceeded(
                    "a story pack is already being made for this family",
                    active=run,
                )
        today = datetime.now(UTC).date()
        started_today = 0
        for run in runs:
            created = run.created_at
            if created.tzinfo is None:  # records persisted before tz-aware writes
                created = created.replace(tzinfo=UTC)
            if created.date() == today:
                started_today += 1
        if started_today >= self._settings.parent_daily_run_cap:
            raise RunCapExceeded("that's all the story packs for today — tomorrow brings more")
```

`src/api/routes/workshop.py`: delete the local `OPERATOR_TOKEN = "operator"` (line 48) and add `OPERATOR_TOKEN` to the existing `from src.workshop.manager import ...` import line.

- [ ] **Step 4: Run the full workshop + api suites**

Run: `uv run pytest tests/workshop/ tests/api/ -v`
Expected: all pass — new cap tests green, no workshop-route regressions (operator paths exempt).

- [ ] **Step 5: Lint**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/config.py src/workshop/manager.py src/api/routes/workshop.py tests/workshop/test_manager.py
git commit -m "feat(workshop): per-family run caps in RunManager.submit, operator exempt (AI-411)"
```

---

### Task 2: Router prefix + sign-in page

**Files:**
- Modify: `src/api/routes/parent.py`
- Create: `src/templates/parent/base.html`, `src/templates/parent/signin.html`
- Test: `tests/api/test_parent_pages.py` (create)

**Interfaces:**
- Consumes: `require_parent_candidate`, `CandidateContext` (AI-410); `Settings.clerk_publishable_key/clerk_issuer/clerk_jwks_url`; `tests/api/clerk_jwt.py` helpers.
- Produces (Tasks 3–4 rely on): `router = APIRouter(prefix="/parent")` (provision route path becomes `"/api/provision"`, public URL unchanged); `templates = Jinja2Templates(directory=TEMPLATES_DIR)` module global (same `TEMPLATES_DIR` expression as workshop.py); helper `async def _page_identity(request, settings) -> CandidateContext | None` returning `None` for missing/invalid sessions (letting pages render sign-in) while still raising 404 (feature unset) and 403 (disabled) — implemented by calling `require_parent_candidate` and converting only 401 to `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_parent_pages.py`:

```python
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
    client = _signed_in_client(monkeypatch, clerk_settings(), include_family_token=False)
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
```

Check `valid_payload`'s kwargs in `tests/api/clerk_jwt.py` (`include_family_token`, `disabled`, `family_token` — established in AI-410); adapt the TEST code if names differ, never the helpers. `clerk_settings()` uses issuer `""` by default — check its signature: pass `clerk_issuer="https://test.clerk.test"` in these page tests if the FAPI-host assertion requires it (the route derives the host from `clerk_issuer`; when issuer is unset, fall back to deriving from the publishable key exactly like `tests/../scratch` — see Step 3's `_fapi_host`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_parent_pages.py -v`
Expected: FAIL — 404s/missing template errors (`GET /parent` route does not exist yet).

- [ ] **Step 3: Implement**

`src/api/routes/parent.py` — changes:

1. Router + templates (top of file):

```python
from base64 import b64decode
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(prefix="/parent")
```

2. The provision route decorator changes from `"/parent/api/provision"` to `"/api/provision"` (public URL unchanged by the prefix).

3. Identity dispatch + FAPI host helpers:

```python
def _fapi_host(settings: Settings) -> str:
    """Frontend-API host for the ClerkJS CDN script tags.

    Prefer the configured issuer (it IS the frontend API origin); fall back to
    decoding the publishable key (base64 of the host + '$', after the last '_').
    """
    if settings.clerk_issuer:
        return settings.clerk_issuer.removeprefix("https://").removeprefix("http://")
    pk = settings.clerk_publishable_key.get_secret_value()
    encoded = pk.rsplit("_", 1)[-1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return b64decode(padded).decode().rstrip("$")


async def _page_identity(
    request: Request, settings: Settings
) -> CandidateContext | None:
    """Candidate identity for page routes: 401 → None (render sign-in);
    404 (feature unset) and 403 (disabled) propagate unchanged."""
    try:
        return await require_parent_candidate(request, settings)
    except HTTPException as error:
        if error.status_code == 401:
            return None
        raise
```

4. The page route:

```python
@router.get("", response_class=HTMLResponse)
async def parent_home(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    ctx = await _page_identity(request, settings)
    context = {
        "fapi_host": _fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "parent/signin.html", context)
    if ctx.family_token is None:
        # First sign-in: page JS POSTs /parent/api/provision then reloads.
        context["onboarding"] = True
        return templates.TemplateResponse(request, "parent/signin.html", context)
    # Provisioned parents get the packs page — Task 4 fills in the run list;
    # until then render it with an empty runs list.
    return templates.TemplateResponse(
        request, "parent/packs.html", {**context, "runs": [], "cap_message": None}
    )
```

`src/templates/parent/base.html` (mirrors workshop/base.html; same token/font/htmx assets, parent CSS namespace can reuse workshop.css until AI-412 restyles):

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{% block title %}Parent area{% endblock %} · Cantastorie</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@400;500;600;700&family=Literata:opsz,wght@7..72,400;7..72,500;7..72,600&display=swap" rel="stylesheet" />
    <script src="/static/js/palette.js"></script>
    <link rel="stylesheet" href="/static/css/tokens.css" />
    <link rel="stylesheet" href="/static/css/workshop.css" />
    <script src="/static/js/vendor/htmx.min.js" defer></script>
    {% block head %}{% endblock %}
  </head>
  <body>
    <div class="ws-shell">
      {% block content %}{% endblock %}
    </div>
  </body>
</html>
```

`src/templates/parent/signin.html` (the ONLY template family allowed to load Clerk):

```html
{% extends "parent/base.html" %}
{% block title %}Sign in{% endblock %}
{% block head %}
  <script defer crossorigin="anonymous"
          src="https://{{ fapi_host }}/npm/@clerk/ui@1/dist/ui.browser.js"></script>
  <script defer crossorigin="anonymous"
          data-clerk-publishable-key="{{ publishable_key }}"
          src="https://{{ fapi_host }}/npm/@clerk/clerk-js@6/dist/clerk.browser.js"></script>
{% endblock %}
{% block content %}
  <div class="ws-card" {% if onboarding %}data-parent-onboarding{% endif %}>
    <h1 class="ws-title">The parent area</h1>
    {% if onboarding %}
      <p>Setting up your family's shelf…</p>
    {% else %}
      <div id="clerk-sign-in"><p data-signin-fallback hidden>Can't sign in right now — the stories on the shelf still play. Try again in a little while.</p></div>
    {% endif %}
  </div>
  <script>
    window.addEventListener("load", async () => {
      const onboarding = document.querySelector("[data-parent-onboarding]");
      if (onboarding) {
        // First sign-in: mint-or-link, then re-render as a provisioned parent.
        // (The IndexedDB family token adoption arrives with connect-this-device;
        // until then the body is empty and the server mints.)
        const response = await fetch("/parent/api/provision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (response.ok) location.reload();
        return;
      }
      try {
        await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
        if (window.Clerk.isSignedIn) { location.reload(); return; }
        window.Clerk.mountSignIn(document.getElementById("clerk-sign-in"));
      } catch (error) {
        document.querySelector("[data-signin-fallback]").hidden = false;
      }
    });
  </script>
{% endblock %}
```

`src/templates/parent/packs.html` — minimal shell for this task (Task 3 adds the form, Task 4 the list):

```html
{% extends "parent/base.html" %}
{% block title %}Your story packs{% endblock %}
{% block content %}
  <h1 class="ws-title">Your story packs</h1>
{% endblock %}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/ -v`
Expected: all pass — new page tests AND the existing `test_parent_provision.py` (URL unchanged proves the prefix refactor is invisible).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`

```bash
git add src/api/routes/parent.py src/templates/parent/ tests/api/test_parent_pages.py
git commit -m "feat(parent): /parent sign-in page with ClerkJS drop-in + identity dispatch (AI-411)"
```

---

### Task 3: Pack request form + `POST /parent/packs`

**Files:**
- Modify: `src/api/routes/parent.py`
- Modify: `src/templates/parent/packs.html`
- Test: `tests/api/test_parent_pages.py` (append)

**Interfaces:**
- Consumes: `require_parent` (full dependency — provisioned parents only), `RunManager` via the SAME DI seam workshop uses (`from src.api.routes.workshop import Manager, get_run_manager` — import `get_run_manager` and declare a local `Manager = Annotated[RunManager, Depends(get_run_manager)]` in parent.py rather than importing workshop's annotation, keeping the modules decoupled), `RunCapExceeded` (Task 1), `PackRequest`, `BackgroundTasks`.
- Produces: `POST /parent/packs` (form-encoded theme/language/count/premise) → 303 redirect to `/parent`; on `RunCapExceeded` → 200 render of `packs.html` with `cap_message` and (if active) the active run's state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_parent_pages.py`:

```python
class _FakeManager:
    """Records submit calls; optionally raises RunCapExceeded."""

    def __init__(self, raise_cap: RunCapExceeded | None = None) -> None:
        self.submits: list[tuple[str, Any]] = []
        self.executed: list[Any] = []
        self.raise_cap = raise_cap

    async def submit(self, family_token: str, request: Any) -> Any:
        if self.raise_cap is not None:
            raise self.raise_cap
        self.submits.append((family_token, request))
        return new_run(family_token, request)

    async def execute(self, record: Any) -> Any:
        self.executed.append(record)
        return record


def _packs_client(
    monkeypatch: pytest.MonkeyPatch,
    manager: _FakeManager,
    *,
    family_token: str = VALID_TOKEN,
) -> TestClient:
    private_key = generate_rsa_keypair()
    monkeypatch.setattr(auth_module, "_fetch_jwks", make_mock_fetch(private_key))
    settings = clerk_settings()
    app = _make_app(settings)
    app.dependency_overrides[get_run_manager] = lambda: manager
    token = mint_token(private_key, valid_payload(family_token=family_token))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_pack_request_submits_under_session_family_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeManager()
    client = _packs_client(monkeypatch, manager)
    response = client.post(
        "/parent/packs",
        data={"theme": "friendship", "language": "it", "count": "1", "premise": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/parent"
    assert len(manager.submits) == 1
    assert manager.submits[0][0] == VALID_TOKEN  # session token, no form override
    assert len(manager.executed) == 1  # background execution kicked off


def test_form_cannot_override_family_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenancy rule: a posted family_token field is ignored entirely."""
    manager = _FakeManager()
    client = _packs_client(monkeypatch, manager)
    client.post(
        "/parent/packs",
        data={"theme": "friendship", "language": "it", "count": "1", "family_token": "f" * 32},
        follow_redirects=False,
    )
    assert manager.submits[0][0] == VALID_TOKEN


def test_cap_hit_renders_friendly_message_with_active_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = new_run(VALID_TOKEN, PackRequest(theme="friendship", language="it", count=1))
    running = active.advance("running")
    manager = _FakeManager(
        raise_cap=RunCapExceeded("a story pack is already being made", active=running)
    )
    client = _packs_client(monkeypatch, manager)
    response = client.post(
        "/parent/packs",
        data={"theme": "friendship", "language": "it", "count": "1"},
    )
    assert response.status_code == 200
    assert "already being made" in response.text
    assert "running" in response.text  # the active run's state is shown


def test_unauthenticated_pack_post_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(clerk_settings())
    response = TestClient(app).post(
        "/parent/packs", data={"theme": "friendship", "language": "it", "count": "1"}
    )
    assert response.status_code == 401
```

Add the needed imports at the top: `from src.workshop.manager import RunCapExceeded`, `from src.workshop.records import PackRequest, new_run`, `from src.api.routes.parent import get_run_manager` (re-exported — see Step 3). Use a valid `Theme` literal from `src/pipeline/models.py` — check it and substitute if `"friendship"` is not one (adapt tests, not models).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_parent_pages.py -v -k "pack or cap_hit"`
Expected: FAIL — 405/ImportError (`POST /parent/packs` does not exist).

- [ ] **Step 3: Implement**

In `src/api/routes/parent.py`:

```python
from fastapi import BackgroundTasks, Form
from fastapi.responses import RedirectResponse

from src.api.auth import ParentContext, require_parent
from src.api.routes.workshop import get_run_manager  # shared DI seam, overridable in tests
from src.workshop.manager import RunCapExceeded, RunManager
from src.workshop.records import PackRequest

Manager = Annotated[RunManager, Depends(get_run_manager)]


@router.post("/packs")
async def request_pack(
    request: Request,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
    background: BackgroundTasks,
    theme: Annotated[str, Form()],
    language: Annotated[str, Form()],
    count: Annotated[int, Form()] = 1,
    premise: Annotated[str, Form()] = "",
) -> Response:
    pack = PackRequest(theme=theme, language=language, count=count, premise=premise or None)  # type: ignore[arg-type]
    try:
        record = await manager.submit(ctx.family_token, pack)
    except RunCapExceeded as cap:
        context = {
            "fapi_host": _fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
            "runs": [],
            "cap_message": str(cap),
            "cap_active": cap.active,
        }
        return templates.TemplateResponse(request, "parent/packs.html", context)
    background.add_task(manager.execute, record)
    return RedirectResponse("/parent", status_code=303)
```

(`Response` from `fastapi`; the return annotation covers both branches.) Extend `packs.html`:

```html
{% extends "parent/base.html" %}
{% block title %}Your story packs{% endblock %}
{% block content %}
  <h1 class="ws-title">Your story packs</h1>

  {% if cap_message %}
    <div class="ws-card" data-cap-message>
      <p>{{ cap_message }}</p>
      {% if cap_active %}<p>Your current pack is <strong>{{ cap_active.state }}</strong>.</p>{% endif %}
    </div>
  {% endif %}

  <form method="post" action="/parent/packs" class="ws-card">
    <label>Theme
      <select name="theme">
        {% for theme in themes %}<option value="{{ theme }}">{{ theme }}</option>{% endfor %}
      </select>
    </label>
    <label>Language
      <select name="language">
        {% for language in languages %}<option value="{{ language }}">{{ language }}</option>{% endfor %}
      </select>
    </label>
    <label>How many stories
      <select name="count"><option>1</option><option>2</option><option>3</option></select>
    </label>
    <label>A wish for the story (optional)
      <input name="premise" maxlength="300" />
    </label>
    <button type="submit" class="ws-pill ws-pill-primary">Make our stories</button>
  </form>
{% endblock %}
```

Populate `themes`/`languages` in both render sites from the pipeline's literals — check how `src/templates/workshop/dashboard.html` builds its form's options and use the same source (`src/pipeline/models.py` `Theme`/`Language`); mirror workshop.py's approach exactly.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/ tests/workshop/ -v`
Expected: all pass.

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`

```bash
git add src/api/routes/parent.py src/templates/parent/packs.html tests/api/test_parent_pages.py
git commit -m "feat(parent): pack request form posting under the session family token (AI-411)"
```

---

### Task 4: My-packs list + progress polling, tenancy-scoped

**Files:**
- Modify: `src/api/routes/parent.py` (`parent_home` fills `runs`; new `GET /packs/{run_id}/progress`)
- Modify: `src/templates/parent/packs.html` (run list)
- Modify: `src/templates/workshop/_progress.html` (parametrize `base_url` / `is_operator` with workshop-preserving defaults)
- Test: `tests/api/test_parent_pages.py` (append — includes the load-bearing cross-tenant tests)

**Interfaces:**
- Consumes: `RunStore.list_runs(family_token=...)` via `manager.store`; `_progress.html`'s context contract (`record`, `live`, `steps`, `staged_stories` — read `run_progress` in workshop.py:266 for the exact context it builds and mirror it).
- Produces: `GET /parent/packs/{run_id}/progress` returning the parametrized `_progress.html` with `base_url="/parent/packs"` and `is_operator=False`; 404 for a run_id not owned by the session's family.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_parent_pages.py`:

```python
def _store_with_runs(manager: _FakeManager, records: list[Any]) -> None:
    class _FakeStore:
        def list_runs(self, *, family_token: str | None = None, state: Any = None) -> list[Any]:
            return [r for r in records if family_token is None or r.family_token == family_token]

        def load(self, family_token: str, run_id: str) -> Any:
            for r in records:
                if r.family_token == family_token and r.id == run_id:
                    return r
            return None

    manager.store = _FakeStore()  # type: ignore[assignment]


def test_my_packs_lists_only_this_familys_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = new_run(VALID_TOKEN, PackRequest(theme="friendship", language="it", count=1))
    other = new_run("f" * 32, PackRequest(theme="friendship", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [mine, other])
    client = _packs_client(monkeypatch, manager)
    response = client.get("/parent")
    assert response.status_code == 200
    assert mine.id in response.text
    assert other.id not in response.text


def test_cross_tenant_progress_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE tenancy test: family A cannot view family B's run — by URL guessing either."""
    others = new_run("f" * 32, PackRequest(theme="friendship", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [others])
    client = _packs_client(monkeypatch, manager)  # session = VALID_TOKEN (family A)
    assert client.get(f"/parent/packs/{others.id}/progress").status_code == 404


def test_own_progress_fragment_polls_parent_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = new_run(VALID_TOKEN, PackRequest(theme="friendship", language="it", count=1))
    manager = _FakeManager()
    _store_with_runs(manager, [mine])
    client = _packs_client(monkeypatch, manager)
    response = client.get(f"/parent/packs/{mine.id}/progress")
    assert response.status_code == 200
    assert f"/parent/packs/{mine.id}/progress" in response.text  # hx-get, not /workshop
    assert "/workshop" not in response.text  # no operator URLs leak to parents
    assert "Delete run" not in response.text  # operator controls hidden


def test_workshop_progress_fragment_is_unchanged_for_operator() -> None:
    """The parametrization must not alter the workshop's rendering defaults."""
    from src.api.routes import workshop as workshop_module  # noqa: PLC0415

    # smoke: the workshop progress route still renders with /workshop URLs.
    # Covered fully by the existing tests/workshop/test_routes.py suite —
    # this test just pins the default base_url in the template.
    source = (
        workshop_module.TEMPLATES_DIR / "workshop" / "_progress.html"
    ).read_text()
    assert 'base_url | default("/workshop/runs")' in source.replace("'", '"')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_parent_pages.py -v -k "packs_lists or cross_tenant or progress"`
Expected: FAIL — progress route missing / template not parametrized.

- [ ] **Step 3: Implement**

`src/templates/workshop/_progress.html`: at the very top add

```jinja
{% set base_url = base_url | default("/workshop/runs") %}
{% set is_operator = is_operator | default(true) %}
```

then replace the two hard-coded operator URLs with the variable — `hx-get="{{ base_url }}/{{ record.id }}/progress"` and wrap the operator-only blocks (`ws-review-pills` staged links, the failed-state "Run it again" form, and the "Delete run" form) in `{% if is_operator %}…{% endif %}`. For parents in `staged` state show instead (inside the `else` of the review-pills guard):

```jinja
{% if not is_operator and record.state == "staged" %}
  <div class="ws-progress-sub">Your stories are made — they'll appear on the shelf after review.</div>
{% endif %}
```

Workshop rendering is unchanged because every default preserves today's values — verify by rendering both routes in the test run.

`src/api/routes/parent.py` — fill the run list in `parent_home` (replace the `"runs": []` placeholder):

```python
    runs = manager.store.list_runs(family_token=ctx.family_token)
    runs.sort(key=lambda r: r.created_at, reverse=True)
    return templates.TemplateResponse(
        request,
        "parent/packs.html",
        {**context, "runs": runs, "cap_message": None, "live": ["queued", "running"]},
    )
```

(`parent_home` gains the `manager: Manager` parameter.) Add the progress route — mirror workshop.py's `run_progress` context exactly (read workshop.py:266-285 for `live`/`steps`/`staged_stories` construction; `steps` comes from `_checkpointed_steps` — import it from workshop or inline the same call):

```python
@router.get("/packs/{run_id}/progress", response_class=HTMLResponse)
async def pack_progress(
    request: Request,
    run_id: str,
    ctx: Annotated[ParentContext, Depends(require_parent)],
    settings: Annotated[Settings, Depends(get_settings)],
    manager: Manager,
) -> HTMLResponse:
    record = manager.store.load(ctx.family_token, run_id)  # tenancy: load is family-scoped
    if record is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "workshop/_progress.html",
        {
            "record": record,
            "live": ["queued", "running"],
            "steps": _checkpointed_steps(record, settings),
            "staged_stories": [],
            "base_url": "/parent/packs",
            "is_operator": False,
        },
    )
```

`packs.html` run list (insert between the cap message and the form):

```html
  {% for record in runs %}
    <div class="ws-card" data-run-id="{{ record.id }}">
      <div>{{ record.request.count }} × {{ record.request.theme }} · {{ record.request.language }}</div>
      <div hx-get="/parent/packs/{{ record.id }}/progress" hx-trigger="load" hx-swap="outerHTML"></div>
    </div>
  {% endfor %}
```

- [ ] **Step 4: Run both suites**

Run: `uv run pytest tests/api/ tests/workshop/ -v`
Expected: all pass — including every pre-existing workshop route test (proves the template parametrization is invisible to the operator face).

- [ ] **Step 5: Lint + commit**

Run: `uv run ruff format . && uv run ruff check --fix . && uv run ruff check .`

```bash
git add src/api/routes/parent.py src/templates/parent/packs.html src/templates/workshop/_progress.html tests/api/test_parent_pages.py
git commit -m "feat(parent): my-packs list + tenancy-scoped progress polling (AI-411)"
```

---

### Task 5: Clerk-containment guard test + final wave

**Files:**
- Test: `tests/api/test_parent_pages.py` (append)

- [ ] **Step 1: Write the guard test**

```python
def test_clerk_loads_nowhere_outside_parent_templates() -> None:
    """Settled (ADR-003): the child player and workshop never load Clerk.

    Scans every template outside src/templates/parent/ and every static JS
    file for Clerk hostnames/script markers.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent / "src"
    pattern = re.compile(r"clerk", re.IGNORECASE)
    offenders: list[str] = []
    for path in (root / "templates").rglob("*.html"):
        if "templates/parent" in str(path).replace("\\", "/"):
            continue
        if pattern.search(path.read_text()):
            offenders.append(str(path))
    for path in (root / "static" / "js").rglob("*.js"):
        if pattern.search(path.read_text()):
            offenders.append(str(path))
    assert offenders == [], f"Clerk reference outside /parent templates: {offenders}"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/api/test_parent_pages.py::test_clerk_loads_nowhere_outside_parent_templates -v`
Expected: PASS (if it fails, a Clerk reference leaked — fix the leak, never the test).

- [ ] **Step 3: Final verification wave**

Run: `uv run pytest` (expect: full suite green) · `uv run ruff format --check . && uv run ruff check .` (clean) · `npm test` (JS regression guard — no JS changed) · `grep -rn "clerk" pyproject.toml | wc -l` → 0.

- [ ] **Step 4: Commit**

```bash
git add tests/api/test_parent_pages.py
git commit -m "test(parent): guard — Clerk assets confined to /parent templates (AI-411)"
```

---

## Final Verification Wave

- [ ] `uv run pytest` — whole suite green (api + workshop + pipeline + js-adjacent).
- [ ] `uv run ruff format --check . && uv run ruff check .` — clean.
- [ ] Live smoke against the Clerk dev instance (`.env` already in this worktree): `uv run uvicorn src.api.main:app --port 8411`, then (1) `curl -s localhost:8411/parent | grep clerk.browser.js` → sign-in page; (2) mint a session JWT via `clerk api /sessions` + `/sessions/{id}/tokens` for the existing provisioned test user and confirm `GET /parent` renders the packs page and `POST /parent/packs` 303s (do NOT let a real pipeline run start against paid APIs — override `get_run_manager` is not possible via curl, so either stop after observing the 303 + run record in R2 pending/, or delete the run record after).
- [ ] Acceptance recap vs AI-411: sign-in page ✓, request form → `manager.submit(family_token, …)` ✓, my-packs + polling ✓, caps (active + daily, operator exempt) ✓, cross-tenant 404 ✓, unset-config 404 ✓, no Clerk outside `/parent` ✓ (guard test).
