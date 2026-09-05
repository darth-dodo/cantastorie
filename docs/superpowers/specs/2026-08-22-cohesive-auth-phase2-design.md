# Cohesive Auth Flow — Phase 2: One Sign-In Surface + ClerkJS Unification

> Issue: **AI-431**. Phase 1 (**AI-430**, #75) shipped role dispatch and dead-end removal; this closes that design's §2/§4 — the visual/frontend half. Approach **A** chosen 2026-08-22.

## Goal

The two Clerk-gated surfaces (`/workshop`, `/parent`) read as one product: one shared sign-in template, one shared base for gated pages, one auth JS module — and the parent surface gains the resilience (session refresh, sign-out, HTMX 401 recovery) the workshop already has. The child player stays Clerk-free.

## Problem (as built today)

- **Two ClerkJS majors from two origins.** Workshop loads `clerk-js@5` async from jsdelivr with the publishable key in `<meta>`; parent loads `@clerk/ui@1` + `clerk-js@6` defer from the instance Frontend-API host with the key on `data-clerk-publishable-key`.
- **Two sign-in templates, two wordmarks, two mount ids.** `workshop/login.html` ("Workshop — the room behind the piazza", `#clerk-signin`) vs `parent/signin.html` ("The parent area", `#clerk-sign-in`), each extending its own base.
- **Divergent auth JS.** `workshop.js` carries an `initClerk()` section (poll-for-Clerk → `Clerk.load()`, mount with `afterSignInUrl: /workshop`, sign-out pill wiring, `htmx:responseError` 401→reload). Parent's logic is inline in `signin.html`: onboarding provision via `POST /parent/api/provision`, signed-in→reload, mount, error-fallback message.
- **Parent authed pages have none of it.** `parent/base.html` has no session refresh, no sign-out button, no 401 handler — a lapsed session mid-poll silently breaks the packs page.
- Operator UI concerns and auth concerns are mixed in one file (`workshop.js`).

## Decision — Approach A: clerk-js@6 + @clerk/ui@1 from the FAPI host, everywhere

Scripts come from the project's own Clerk instance domain, dropping jsdelivr as a third-party origin entirely (consistent with the privacy posture behind AI-384). The pattern is proven live on `/parent` since AI-411 and already asserted by tests. v5 is an aging major served from a CDN we would rather not depend on.

Rejected alternatives:

- **B — v5/jsdelivr everywhere:** migrates the newer proven surface backwards onto the aging major plus a third-party CDN.
- **C — vendor/self-host the bundle:** strongest containment story but real vendoring/CSP work for two adult surfaces while the child player stays Clerk-free regardless. Revisit only if CSP hardening lands (AI-413 territory).

## Design

### 1. One sign-in template

New `src/templates/auth/` directory:

- `auth/base.html` — the shared shell for every Clerk-gated page: fonts, `palette.js`, `tokens.css`, `workshop.css`, htmx, the two Clerk scripts (defer, crossorigin, from `https://{{ fapi_host }}/npm/@clerk/ui@1/…` then `…/@clerk/clerk-js@6/…`, key on `data-clerk-publishable-key`), the sign-out pill, and `auth.js`.
- `auth/sign_in.html` — extends `auth/base.html`. One wordmark ("Cantastorie"), one tagline, one mount div (`<div id="clerk-sign-in" data-clerk-mount>`). Carries:
  - `data-auth-door="workshop|parent"` on `<body>` (set by the rendering route),
  - the `{% if onboarding %}` branch ("Setting up your family's shelf…"),
  - the `[data-signin-fallback]` message.

`workshop/login.html`, `parent/signin.html`, `workshop/base.html`, `parent/base.html` are deleted; `dashboard.html`, `run.html`, `story.html`, `packs.html` re-point `extends` at `auth/base.html`. Whichever door an unauthenticated user hits renders this identical page; Clerk's `afterSignInUrl` returns them to that door and Phase 1 dispatch routes them home — the entry door no longer matters.

### 2. One auth module

New `src/static/js/auth.js` (loaded by `auth/base.html` on every gated page):

1. Wait for Clerk (same brief poll pattern workshop uses today), then `Clerk.load({ ui: { ClerkUI } })`.
2. **Sign-in duties** (only when `[data-clerk-mount]` exists): if already signed in → reload; otherwise `mountSignIn` with `afterSignInUrl`/`afterSignUpUrl` = the path for the current door (`data-auth-door`: `workshop → /workshop`, `parent → /parent`; Phase 1 dispatch then routes each role to their home).
3. **Onboarding duty** (only when `[data-parent-onboarding]` exists): POST `/parent/api/provision` → reload on success; failure → reveal fallback text.
4. **Every authed page:** reveal and wire the sign-out pill (`Clerk.signOut({ redirectUrl: <same door path> })`).
5. **Session-lapse recovery:** `htmx:responseError` with status 401 → full-page reload → sign-in, on both surfaces.

`workshop.js` loses its Clerk section (initClerk, poll, sign-out, 401 handler) and keeps only operator UI behavior. Mount ids unify to `#clerk-sign-in`.

### 3. Route surface

`workshop._sign_in_page` and the parent sign-in route render `auth/sign_in.html`, passing `fapi_host`, the publishable key, and the door. Dispatch matrix, 404-when-unconfigured, and disabled→403 invariants are unchanged (Phase 1).

### 4. Containment

The child player gains nothing and loses nothing: no template or JS under the player path references Clerk, asserted by the existing guard (`test_clerk_loads_nowhere_in_the_child_player`), which must stay green untouched.

## Components touched (guidance for the plan)

- `src/templates/auth/{base.html,sign_in.html}` — new; four templates deleted; four re-pointed.
- `src/static/js/auth.js` — new; `src/static/js/workshop.js` Clerk section removed.
- `src/api/routes/workshop.py`, `src/api/routes/parent.py` — render the shared sign-in; pass door + host + key.
- Tests — see below.

## Testing

- **Route tests (pytest):**
  - Every gated page (both areas) loads clerk-js@6 + ui@1 from the FAPI host with `data-clerk-publishable-key` (replaces the meta-tag assertion).
  - Both doors render the same sign-in markup modulo `data-auth-door`; unconfigured Clerk still 404s both doors.
  - Authed pages (operator dashboard, parent packs) include `auth.js` and show the sign-out pill.
- **JS unit tests (Vitest/jsdom):** 401→reload handler; fallback reveal on load failure; mount gating (no mount when signed in / when mount element absent). Clerk mocked; no network.
- **Guards stay green untouched:** child-player containment; disabled→403.
- **Manual browser matrix:** unauth both doors → identical sign-in; operator → lands `/workshop`; unprovisioned parent → provision → packs; provisioned parent → lands `/parent`; sign-out works from both areas; expired-session HTMX request recovers to sign-in.

## Out of scope

- CSP headers (AI-413), Playwright E2E journey (AI-414), font self-hosting (AI-384).
- Superuser *parent* view; any restyling beyond the unified shell; child-player changes of any kind.

## Acceptance

- One sign-in surface, one base, one auth module; zero references to clerk-js@5 or jsdelivr anywhere.
- Parent authed pages have session refresh, sign-out, and 401 recovery identical to the workshop's.
- All pytest + Vitest suites green; containment guard green; `make check` clean.
