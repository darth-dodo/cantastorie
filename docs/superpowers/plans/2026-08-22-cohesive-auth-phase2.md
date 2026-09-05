# Cohesive Auth Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One shared Clerk sign-in surface + one auth JS module across `/workshop` and `/parent`, standardized on clerk-js@6 + @clerk/ui@1 from the instance FAPI host.

**Architecture:** New `src/templates/auth/{base.html,sign_in.html}` replace four templates; new `src/static/js/auth.js` carries all Clerk behavior; `fapi_host()` joins `home_path()` in `src/api/routes/_nav.py`. Routes pass `door`/`fapi_host`/`publishable_key`; the door drives `afterSignInUrl`, sign-out redirect, and Phase 1 dispatch finishes routing.

**Tech Stack:** FastAPI + Jinja2, vanilla IIFE script (house style — no bundler), Vitest/jsdom for auth.js, pytest for routes. Spec: `docs/superpowers/specs/2026-08-22-cohesive-auth-phase2-design.md`.

## Global Constraints

- No new dependencies; no bundler; child player untouched (`test_clerk_loads_nowhere_in_the_child_player` stays green unmodified).
- Clerk scripts only from `https://{{ fapi_host }}/npm/...` — zero jsdelivr references may survive.
- Mount id is `clerk-sign-in`; door travels as `<body data-auth-door="workshop|parent">`.
- Publishable key only via `data-clerk-publishable-key` on the script tags (meta tag variant dies).
- Python: ruff format, 100-char lines, strict mypy. Conventional commits with `(AI-431)`.
- Historical docs (old plans/specs) are never rewritten.

---

### Task 1: Shared sign-in surface — templates, `_nav.fapi_host`, route swap

**Files:**
- Create: `src/templates/auth/base.html`
- Create: `src/templates/auth/sign_in.html`
- Modify: `src/api/routes/_nav.py` (append `fapi_host`)
- Modify: `src/api/routes/workshop.py:106-109` (`_sign_in_page`)
- Modify: `src/api/routes/parent.py:86-92` (`_fapi_host` → deleted), `:113-124` (two render sites)
- Modify: `src/templates/workshop/dashboard.html:1`, `run.html:1`, `story.html:1`, `parent/packs.html:1` (extends line)
- Delete: `src/templates/workshop/login.html`, `src/templates/workshop/base.html`, `src/templates/parent/signin.html`, `src/templates/parent/base.html`
- Test: `tests/workshop/test_routes.py`, `tests/api/test_parent_pages.py`

**Interfaces:**
- Consumes: existing `Settings` (`clerk_issuer`, `clerk_publishable_key`), `home_path(is_operator)` in `_nav.py`.
- Produces: `fapi_host(settings: Settings) -> str` in `_nav.py`; template contract — every gated template receives `door: str`, `fapi_host: str`, `publishable_key: str` (+ `onboarding: bool` on the parent sign-in render only). Later tasks rely on exactly these names.

- [ ] **Step 1: Write failing tests**

In `tests/workshop/test_routes.py`, replace the assertions inside `test_every_workshop_page_loads_clerk_js` and the two `'id="clerk-signin"'` checks:

```python
def test_unauthenticated_gets_shared_sign_in(tmp_path: Path, s3: S3Client) -> None:
    s = clerk_settings()
    client = TestClient(_make_app(s, tmp_path))
    page = client.get("/workshop")
    assert page.status_code == 200
    assert 'id="clerk-sign-in"' in page.text
    assert 'data-auth-door="workshop"' in page.text
    # v6 + ui@1 from the instance FAPI host — never jsdelivr.
    assert "test.clerk.test/npm/@clerk/ui@1" in page.text
    assert "test.clerk.test/npm/@clerk/clerk-js@6" in page.text
    assert "jsdelivr" not in page.text
```

Update `test_every_workshop_page_loads_clerk_js` body to:

```python
        assert "npm/@clerk/clerk-js@6" in page.text
        assert "npm/@clerk/ui@1" in page.text
        assert 'data-clerk-publishable-key="pk_test_xxx"' in page.text
```

(dropping the `name="clerk-publishable-key"` meta assertion), and change the two remaining `'id="clerk-signin"'` literals to `'id="clerk-sign-in"'`.

In `tests/api/test_parent_pages.py`, extend `test_anonymous_gets_sign_in_page_with_clerk_script` with:

```python
    assert "npm/@clerk/ui@1" in response.text
    assert 'data-auth-door="parent"' in response.text
```

Run: `uv run pytest tests/workshop/test_routes.py tests/api/test_parent_pages.py -x -q`
Expected: FAIL — pages still render the old templates (`id="clerk-signin"`, jsdelivr URL).

- [ ] **Step 2: Move `fapi_host` into `_nav.py`**

Append to `src/api/routes/_nav.py` (add imports at top: `from base64 import b64decode`, `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from src.config import Settings`):

```python
def fapi_host(settings: Settings) -> str:
    """The Clerk Frontend API host scripts load from: the issuer host when
    set, else the domain encoded in the publishable key's suffix."""
    if settings.clerk_issuer:
        return settings.clerk_issuer.removeprefix("https://").removeprefix("http://")
    pk = settings.clerk_publishable_key.get_secret_value()
    encoded = pk.rsplit("_", 1)[-1]
    padded = encoded + "=" * (-len(encoded) % 4)
    return b64decode(padded).decode().rstrip("$")
```

Delete `_fapi_host` from `parent.py`; replace its two uses with the imported name.

- [ ] **Step 3: Write the shared templates**

`src/templates/auth/base.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{% block title %}Cantastorie{% endblock %} · Cantastorie</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@400;500;600;700&family=Literata:opsz,wght@7..72,400;7..72,500;7..72,600&display=swap" rel="stylesheet" />
    <script src="/static/js/palette.js"></script>
    <link rel="stylesheet" href="/static/css/tokens.css" />
    <link rel="stylesheet" href="/static/css/workshop.css" />
    <script src="/static/js/vendor/htmx.min.js" defer></script>
    <script defer crossorigin="anonymous"
            data-clerk-publishable-key="{{ publishable_key }}"
            src="https://{{ fapi_host }}/npm/@clerk/ui@1/dist/ui.browser.js"></script>
    <script defer crossorigin="anonymous"
            data-clerk-publishable-key="{{ publishable_key }}"
            src="https://{{ fapi_host }}/npm/@clerk/clerk-js@6/dist/clerk.browser.js"></script>
    {% block head %}{% endblock %}
  </head>
  <body data-auth-door="{{ door }}">
    <div class="ws-shell">
      <button type="button" id="ws-signout" class="ws-pill ws-signout" hidden>Sign out</button>
      {% block content %}{% endblock %}
    </div>
    <script src="/static/js/auth.js" defer></script>
  </body>
</html>
```

`src/templates/auth/sign_in.html`:

```html
{% extends "auth/base.html" %}
{% block title %}Sign in{% endblock %}
{% block content %}
<div class="ws-login-wrap">
  <div class="ws-wordmark-stack">
    <div class="ws-wordmark">Cantastorie</div>
    <div class="ws-tagline">bedtime stories your family speaks</div>
  </div>
  <div class="ws-card ws-login-card">
    {% if onboarding %}
      <p data-parent-onboarding>Setting up your family's shelf…</p>
    {% else %}
      <div id="clerk-sign-in" data-clerk-mount>
        <p data-signin-fallback hidden>Can't sign in right now — the stories on the shelf still play. Try again in a little while.</p>
      </div>
    {% endif %}
  </div>
  <p class="ws-mono-note">sign in with your account —<br>with Clerk unconfigured, this area answers 404</p>
</div>
{% endblock %}
```

- [ ] **Step 4: Swap the routes**

`workshop.py` `_sign_in_page` becomes:

```python
def _sign_in_page(request: Request, settings: Settings, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/sign_in.html",
        {
            "door": "workshop",
            "fapi_host": fapi_host(settings),
            "publishable_key": settings.clerk_publishable_key.get_secret_value(),
        },
        status_code=status_code,
    )
```

(importing `fapi_host` alongside `home_path` from `src.api.routes._nav`). In `parent.py`, add `fapi_host` to the existing `_nav` import at `:29`, delete `_fapi_host`, and make the two render sites:

```python
    context = {
        "door": "parent",
        "fapi_host": fapi_host(settings),
        "publishable_key": settings.clerk_publishable_key.get_secret_value(),
    }
    if ctx is None:
        return templates.TemplateResponse(request, "auth/sign_in.html", context)
    context["onboarding"] = True
    return templates.TemplateResponse(request, "auth/sign_in.html", context)
```

Re-point the extends line in `dashboard.html`, `run.html`, `story.html`, `packs.html` to `{% extends "auth/base.html" %}`. Delete the four old templates.

- [ ] **Step 5: Run tests to green**

Run: `uv run pytest tests/workshop tests/api -x -q && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: all PASS (containment guard included).

- [ ] **Step 6: Commit**

```bash
git add -A src/ tests/
git commit -m "feat(auth): one shared Clerk sign-in surface on clerk-js@6 from FAPI host (AI-431)"
```

---

### Task 2: `auth.js` — one module for sign-in, provision, sign-out, 401 recovery

**Files:**
- Create: `src/static/js/auth.js`
- Test: `tests/js/auth.test.js`

**Interfaces:**
- Consumes: DOM contract from Task 1 (`data-auth-door`, `[data-clerk-mount]`, `[data-parent-onboarding]`, `[data-signin-fallback]`, `#ws-signout`); globals `window.Clerk`, `window.__internal_ClerkUICtor`.
- Produces: side-effect module only (IIFE, house style) — no exports.

- [ ] **Step 1: Write failing Vitest tests**

`tests/js/auth.test.js`:

```javascript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function mountDom(bodyHtml, door) {
  document.body.innerHTML = bodyHtml;
  document.body.setAttribute("data-auth-door", door);
}

function fakeClerk(overrides = {}) {
  window.__internal_ClerkUICtor = function ClerkUI() {};
  window.Clerk = {
    load: vi.fn().mockResolvedValue(undefined),
    mountSignIn: vi.fn(),
    signOut: vi.fn(),
    isSignedIn: false,
    user: null,
    ...overrides,
  };
}

async function loadAuth() {
  vi.resetModules();
  await import("../../src/static/js/auth.js");
  // the module's init() is async; flush a microtask + its first timer tick
  await new Promise((r) => setTimeout(r, 60));
}

describe("auth.js", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.Clerk;
    delete window.__internal_ClerkUICtor;
  });

  it("mounts sign-in on an unauthenticated sign-in page", async () => {
    mountDom('<div id="clerk-sign-in" data-clerk-mount></div>', "workshop");
    fakeClerk();
    await loadAuth();
    expect(window.Clerk.load).toHaveBeenCalled();
    expect(window.Clerk.mountSignIn).toHaveBeenCalledWith(
      expect.any(Element),
      { afterSignInUrl: "/workshop", afterSignUpUrl: "/workshop" }
    );
  });

  it("reloads instead of mounting when already signed in", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom('<div data-clerk-mount></div>', "parent");
    fakeClerk({ isSignedIn: true });
    await loadAuth();
    expect(window.Clerk.mountSignIn).not.toHaveBeenCalled();
    expect(reload).toHaveBeenCalled();
  });

  it("provisions on the parent onboarding screen, then reloads", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom("<p data-parent-onboarding></p>", "parent");
    fakeClerk();
    await loadAuth();
    expect(fetch).toHaveBeenCalledWith("/parent/api/provision",
      expect.objectContaining({ method: "POST" }));
    expect(reload).toHaveBeenCalled();
  });

  it("reveals the fallback when Clerk.load fails", async () => {
    mountDom('<p data-signin-fallback hidden></p><div data-clerk-mount></div>', "workshop");
    fakeClerk({ load: vi.fn().mockRejectedValue(new Error("down")) });
    await loadAuth();
    expect(document.querySelector("[data-signin-fallback]").hidden).toBe(false);
  });

  it("wires sign-out on authed pages", async () => {
    mountDom('<button id="ws-signout" hidden></button>', "parent");
    fakeClerk({ user: {} });
    await loadAuth();
    const btn = document.getElementById("ws-signout");
    expect(btn.hidden).toBe(false);
    btn.click();
    expect(window.Clerk.signOut).toHaveBeenCalledWith({ redirectUrl: "/parent" });
  });

  it("reloads on HTMX 401, not on other errors", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom("", "workshop");
    fakeClerk({ user: {} });
    await loadAuth();
    document.body.dispatchEvent(new CustomEvent("htmx:responseError",
      { detail: { xhr: { status: 401 } } }));
    document.body.dispatchEvent(new CustomEvent("htmx:responseError",
      { detail: { xhr: { status: 500 } } }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
```

Run: `npx vitest run tests/js/auth.test.js`
Expected: FAIL — `auth.js` does not exist.

- [ ] **Step 2: Write `src/static/js/auth.js`**

```javascript
/* Shared auth for Clerk-gated surfaces — one module for /workshop and /parent.
   Sign-in duties run only where the sign-in markup exists; authed pages get
   sign-out wiring and HTMX session-lapse recovery. */
(function () {
  "use strict";

  var DOOR_PATHS = { workshop: "/workshop", parent: "/parent" };
  var doorPath =
    DOOR_PATHS[document.body.getAttribute("data-auth-door")] || "/workshop";

  function revealFallback() {
    var fallback = document.querySelector("[data-signin-fallback]");
    if (fallback) fallback.hidden = false;
  }

  function wireSignOut() {
    var signout = document.getElementById("ws-signout");
    if (signout && window.Clerk.user) {
      signout.hidden = false;
      signout.addEventListener("click", function () {
        window.Clerk.signOut({ redirectUrl: doorPath });
      });
    }
  }

  async function init() {
    if (!window.Clerk) return;
    try {
      await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    } catch (error) {
      revealFallback();
      return;
    }
    var onboarding = document.querySelector("[data-parent-onboarding]");
    if (onboarding) {
      // First parent sign-in: mint-or-link the family token, then re-render.
      try {
        var response = await fetch("/parent/api/provision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (response.ok) window.location.reload();
        else revealFallback();
      } catch (error) {
        revealFallback();
      }
      return;
    }
    var mount = document.querySelector("[data-clerk-mount]");
    if (mount) {
      if (window.Clerk.isSignedIn) {
        window.location.reload();
        return;
      }
      window.Clerk.mountSignIn(mount, {
        afterSignInUrl: doorPath,
        afterSignUpUrl: doorPath,
      });
      return;
    }
    wireSignOut();
  }

  init();

  // A lapsed session makes authed HTMX requests 401; a full-page reload
  // drops the user back onto the shared sign-in flow.
  document.body.addEventListener("htmx:responseError", function (e) {
    if (e.detail && e.detail.xhr && e.detail.xhr.status === 401) {
      window.location.reload();
    }
  });
})();
```

- [ ] **Step 3: Run tests to green**

Run: `npx vitest run tests/js/auth.test.js`
Expected: PASS (6/6). If the poll timing flakes, raise the flush delay to 100ms in the test helper — do not add retry logic to the module.

- [ ] **Step 4: Commit**

```bash
git add src/static/js/auth.js tests/js/auth.test.js
git commit -m "feat(auth): unified auth.js — mount, provision, sign-out, htmx 401 recovery (AI-431)"
```

---

### Task 3: Remove workshop.js Clerk section + stale-reference sweep

**Files:**
- Modify: `src/static/js/workshop.js:162-197` (delete everything from the `// Clerk:` comment through the `htmx:responseError` listener at EOF)
- Test: `tests/workshop/test_routes.py` (append sweep test)

**Interfaces:**
- Consumes: nothing new.
- Produces: `workshop.js` contains zero Clerk references; a regression gate proving it stays that way.

- [ ] **Step 1: Write failing sweep test**

Append to `tests/workshop/test_routes.py`:

```python
def test_no_stale_clerk_artifacts_survive() -> None:
    """Phase 2 killed clerk-js@5, jsdelivr, the meta-tag key, and the old
    mount id; nothing anywhere under src/ may bring them back."""
    import re
    from pathlib import Path

    stale = re.compile(
        r"clerk-js@5|cdn\.jsdelivr\.net/npm/@clerk|clerk-publishable-key\" *content=|"
        r'id="clerk-signin"',
    )
    offenders = [
        str(p)
        for p in Path("src").rglob("*")
        if p.suffix in {".html", ".js", ".py"} and stale.search(p.read_text())
    ]
    assert offenders == []
```

Run: `uv run pytest tests/workshop/test_routes.py::test_no_stale_clerk_artifacts_survive -q`
Expected: FAIL — `workshop.js` still contains `id="clerk-signin"` in its mount code, so the sweep finds an offender.

- [ ] **Step 2: Delete the Clerk section from `workshop.js`**

Remove `src/static/js/workshop.js` lines 162–197: the trailing comment block, `initClerk()`, the poll block, and the `htmx:responseError` listener (now owned by `auth.js`). The file ends after the `htmx:afterSwap` re-init listener.

- [ ] **Step 3: Run tests to green**

Run: `uv run pytest tests/workshop/test_routes.py::test_no_stale_clerk_artifacts_survive -q && npx vitest run && uv run pytest -q`
Expected: sweep PASS, full suites PASS.

- [ ] **Step 4: Full checks + commit**

Run: `make check && make test`
Expected: clean.

```bash
git add src/static/js/workshop.js tests/workshop/test_routes.py
git commit -m "refactor(auth): drop workshop.js Clerk section; sweep gate for stale artifacts (AI-431)"
```

---

### Task 4: Browser verification matrix (manual, then hand off)

No code changes. Execute against `make dev` with real Clerk test-mode env:

- [ ] Unauthenticated `/workshop` and `/parent`: identical shared sign-in, one wordmark "Cantastorie".
- [ ] Operator signs in at `/parent` → lands `/workshop`; parent signs in at `/workshop` → lands `/parent` (dispatch unchanged).
- [ ] Unprovisioned parent → "Setting up your family's shelf…" → provision POST → packs.
- [ ] Sign-out pill visible and functional on dashboard AND packs.
- [ ] Expire the session, trigger an HTMX action → full-page reload lands on sign-in.
- [ ] Network tab: Clerk requests go only to the instance host; no jsdelivr.
- [ ] Child player at `/`: zero Clerk requests.

Report results on AI-431 before opening the PR.
