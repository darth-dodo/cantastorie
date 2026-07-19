# Workshop Behind Clerk — Design

**Date**: 2026-07-19
**Status**: Approved design, pre-implementation
**Builds on**: [2026-07-12 Clerk parent auth design](2026-07-12-clerk-parent-auth-family-tenancy-design.md) · [ADR-003](../../adr/ADR-003-parent-authentication-clerk.md) · [ADR-004](../../adr/ADR-004-workshop-area.md)
**Supersedes**: the 2026-07-12 spec's out-of-scope line "`/workshop` keeps the env-secret" — the workshop now moves to Clerk.

---

## Goal

Replace the workshop's `WORKSHOP_SECRET` password gate with Clerk sign-in. Any signed-in Clerk user is an operator; who can sign up is controlled in the Clerk dashboard (restricted/invite-only), not in application code. The env-secret login, its cookie hashing, and the `WORKSHOP_SECRET` setting are deleted.

## Decisions resolved in brainstorming (2026-07-19)

| Question | Decision |
|----------|----------|
| Operator gate | **Any signed-in Clerk user.** No allow-list, no role claim, no family_token check. Access control = Clerk sign-up restrictions. |
| Old secret login | **Removed entirely.** No fallback for local dev; tests mock JWT verification instead. |
| Sign-in UI | **ClerkJS sign-in page** replacing `login.html`: loads ClerkJS with the publishable key, mounts Clerk's prebuilt `<SignIn>` component, returns to `/workshop`. Reusable pattern for the Phase 2 parent UI. |
| Wiring approach | **Shared verifier function** extracted from `src/api/auth.py`; workshop's existing `_authed()` helper swaps its internals to call it. Parent deps rebuilt on the same core — one JWT verification path in the codebase. |

## Out of scope

- Family-scoping of runs (workshop stays operator-global; every operator sees all runs)
- The parent-area UI (`/parent` pages — separate Phase 2 work)
- Any change to the child player (stays Clerk-free, cookie-free)
- Operator roles or per-user permissions

---

## 1. Config

- Delete `workshop_secret` from `src/config.py`.
- The workshop's feature gate becomes the Clerk gate: if `clerk_jwks_url` **or** `clerk_publishable_key` is unset, all `/workshop` routes return 404 — same disabled-by-default pattern as today.
- Remove `WORKSHOP_SECRET` from `render.yaml`, deploy docs, and `.env` examples.

## 2. Server (auth core + workshop routes)

- In `src/api/auth.py`, factor the verification steps — read `__session` cookie → JWKS fetch/cache (existing TTL + stale-if-error behavior unchanged) → RS256 verify with `exp`/`nbf`/optional `iss` → `disabled` kill-switch check — into a shared helper, e.g. `verify_clerk_session(request, settings)`, returning the verified claims or `None`.
- `require_parent_candidate` / `require_parent` are rebuilt on that helper; their observable behavior (status codes, contexts) is unchanged.
- In `src/api/routes/workshop.py`, `_authed(request, settings)` calls the shared helper; any valid session ⇒ operator. All 12 handlers keep their current shape (render login vs. page for GETs, 401 for mutations).
- Delete `POST /workshop/login`, `_session_token()`, and all `workshop_session` cookie logic.

## 3. Sign-in page + ClerkJS on every workshop page

- `login.html` becomes a ClerkJS sign-in page: loads ClerkJS using `clerk_publishable_key` (passed from settings through the template), mounts `<SignIn>`, lands on `/workshop` after sign-in.
- **ClerkJS loads in `base.html` on every workshop page.** Clerk's `__session` JWT is short-lived (~60 s) and refreshed only by ClerkJS running in the browser; without it, HTMX progress polling and any request after the first minute would 401. This is the load-bearing constraint of the design.
- Add a **Sign out** button to the dashboard header: calls `Clerk.signOut()`, then returns to the sign-in page.

## 4. Error handling

- Full-page GET with missing/invalid/expired session → render the sign-in page with status 401 (same pattern as today's login rendering).
- HTMX request that hits a 401 (e.g., token expired in the gap between ClerkJS refreshes) → a small `htmx:responseError` handler in `workshop.js` does a full-page reload, which lands on the sign-in page.
- Clerk unconfigured → 404 on all `/workshop` routes (feature gate, section 1).

## 5. Testing

- Workshop route tests drop the secret-login setup and instead sign a valid RS256 JWT with the RSA-keypair + mock-JWKS fixtures already used by the `auth.py` tests, setting it as the `__session` cookie.
- Cover: authed page loads, unauthenticated GET renders sign-in (401), unauthenticated mutation → 401, expired token → 401, Clerk unconfigured → 404.
- `auth.py`'s existing tests stay green through the refactor (the parent deps' behavior is unchanged).

## 6. Docs

- Update the Clerk setup doc (from AI-410) to note the workshop now requires Clerk and how operators are admitted (Clerk dashboard sign-up restrictions).
- Update the status table in `docs/product.md`.
- Note the superseded out-of-scope line where the 2026-07-12 spec is referenced (this spec's header records it).

## 7. Implementation order

1. Auth refactor: extract shared verifier, rebuild parent deps on it (tests green).
2. Workshop server swap: `_authed()` → Clerk, delete secret login + config, update workshop tests.
3. Frontend: ClerkJS in `base.html`, new `login.html`, sign-out button, HTMX 401 handler.
4. Cleanup + docs: `render.yaml`, `.env` examples, setup doc, product status table.
