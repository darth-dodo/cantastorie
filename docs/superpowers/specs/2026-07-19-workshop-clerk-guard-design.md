# The Workshop, Behind Clerk and Scope-Driven — Design

**Date**: 2026-07-19
**Status**: Approved design, pre-implementation
**Builds on**: [2026-07-12 Clerk parent auth design](2026-07-12-clerk-parent-auth-family-tenancy-design.md) · [ADR-003](../../adr/ADR-003-parent-authentication-clerk.md) · [ADR-004](../../adr/ADR-004-workshop-area.md)
**Supersedes**: (1) the 2026-07-12 spec's out-of-scope line "`/workshop` keeps the env-secret" — the workshop moves to Clerk; (2) the framing of the parent area as a separate `/parent` **surface** — the parent area becomes a **scope of the workshop**, not a second face.

---

## Goal

Two moves, one architecture:

1. **Guard the workshop with Clerk.** Replace the `WORKSHOP_SECRET` password gate with Clerk sign-in. Delete the secret login, its cookie hashing, and the `WORKSHOP_SECRET` setting.
2. **Make the workshop scope-driven.** There is one `/workshop`. What a signed-in user sees and can do is derived server-side from their session: an **operator** works globally (all families, publishes to the shared shelf); a **parent** works within their own family (their runs only, publishes to their family overlay). The parent area stops being a separate `/parent` surface and becomes the workshop scoped to a family.

This slice (AI-426) delivers the operator path and the scope seam. The parent-scoped views and family-overlay publish are follow-on work (AI-411/AI-412, reframed as *extending the workshop* rather than building a parallel surface).

## Decisions resolved in brainstorming (2026-07-19)

| Question | Decision |
|----------|----------|
| Operator vs parent | **`role: "operator"` in the Clerk user's `public_metadata`, surfaced as a session-token claim.** Operators get the global bench; everyone else is a parent, scoped to their `family_token`. This revises the earlier "any signed-in user is an operator" idea — necessary because parents will sign into the same Clerk instance. |
| Surface shape | **One scope-driven `/workshop`.** Single router, single template set; a `WorkshopScope` resolved from the verified session decides data visibility and publish target. Not two routes, not a UI toggle — the boundary is enforced server-side per request. |
| Old secret login | **Removed entirely.** No fallback for local dev; tests mock JWT verification instead. |
| Sign-in UI | **ClerkJS sign-in page** replacing `login.html`: loads ClerkJS with the publishable key, mounts Clerk's prebuilt `<SignIn>` component, returns to `/workshop`. |
| Wiring approach | **Shared verifier** extracted from `src/api/auth.py`; the workshop and the parent deps build on one JWT verification path. |

## Non-operator access in this slice

Parents can't self-serve yet — the parent-scoped views and overlay publish aren't built. Until they are, a signed-in **non-operator** hitting `/workshop` gets **403** (a friendly "your workshop is coming soon" page, not a raw error). Because Clerk sign-up stays restricted, in practice the only accounts today are operators; the 403 is the correct closed-by-default behavior for the seam, not a user-facing state anyone should hit yet.

## Out of scope (this slice)

- Parent-scoped run views and the family-overlay publish path (AI-411/AI-412 — build them *on* the workshop's scope seam)
- Per-family run caps, the pack-request form, the review queue UX
- Any change to the child player (stays Clerk-free, cookie-free)
- Operator sub-roles / granular permissions beyond the single operator flag

---

## 1. Config

- Delete `workshop_secret` from `src/config.py`.
- The workshop's feature gate becomes the Clerk gate: if `clerk_jwks_url` **or** `clerk_publishable_key` is unset, all `/workshop` routes return 404 — same disabled-by-default pattern as today.
- Remove `WORKSHOP_SECRET` from `render.yaml`, deploy docs, and `.env` examples.
- **Clerk session-token template** must include both `role` (from `public_metadata.role`) and the existing `family_token` claim, so the server reads scope straight from the verified JWT with no extra Clerk API call per request. Document this in the setup doc.

## 2. Server — verifier, scope, and workshop routes

- In `src/api/auth.py`, factor the verification steps — read `__session` cookie → JWKS fetch/cache (existing TTL + stale-if-error behavior unchanged) → RS256 verify with `exp`/`nbf`/optional `iss` → `disabled` kill-switch check — into a shared helper `verify_clerk_session(request, settings)`, returning the verified claims or `None`.
- `require_parent_candidate` / `require_parent` are rebuilt on that helper; their observable behavior (status codes, contexts) is unchanged.
- Introduce a small **`WorkshopScope`** value object resolved from the claims:
  - `role == "operator"` → operator scope: sees all families, publish target = shared shelf.
  - otherwise → family scope keyed by `family_token`: sees only that family's runs, publish target = that family's overlay.
- In `src/api/routes/workshop.py`, replace `_authed()` with a dependency that: verifies the session (else render sign-in / 401), then resolves `WorkshopScope`. In this slice, handlers act on operator scope; a family-scoped session short-circuits to the 403 "coming soon" page until the parent views land. The scope object is threaded through handlers now so the parent path is a fill-in, not a refactor, later.
- Delete `POST /workshop/login`, `_session_token()`, and all `workshop_session` cookie logic.

## 3. Sign-in page + ClerkJS on every workshop page

- `login.html` becomes a ClerkJS sign-in page: loads ClerkJS using `clerk_publishable_key` (passed from settings through the template), mounts `<SignIn>`, lands on `/workshop` after sign-in.
- **ClerkJS loads in `base.html` on every workshop page.** Clerk's `__session` JWT is short-lived (~60 s) and refreshed only by ClerkJS running in the browser; without it, HTMX progress polling and any request after the first minute would 401. This is the load-bearing constraint of the design.
- Add a **Sign out** button to the header: calls `Clerk.signOut()`, then returns to the sign-in page.

## 4. Error handling

- Full-page GET with missing/invalid/expired session → render the sign-in page with status 401 (same pattern as today's login rendering).
- Signed-in **non-operator** in this slice → 403 "coming soon" page (see Non-operator access above).
- HTMX request that hits a 401 (token expired between ClerkJS refreshes) → a small `htmx:responseError` handler in `workshop.js` does a full-page reload, landing on the sign-in flow.
- Clerk unconfigured → 404 on all `/workshop` routes (feature gate, section 1).

## 5. Testing

- Workshop route tests drop the secret-login setup and instead sign a valid RS256 JWT with the RSA-keypair + mock-JWKS fixtures already used by the `auth.py` tests, setting it as the `__session` cookie.
- Cover: operator session loads the bench; unauthenticated GET renders sign-in (401); unauthenticated mutation → 401; **non-operator session → 403**; expired token → 401; Clerk unconfigured → 404.
- Add a focused `WorkshopScope` unit test: operator claims → operator scope; family_token-only claims → family scope with the right token and publish target.
- `auth.py`'s existing tests stay green through the refactor.

## 6. Docs

- Update the Clerk setup doc (from AI-410): the workshop now requires Clerk; the session-token template must carry `role` and `family_token`; how an account is made an operator (`public_metadata.role = "operator"` in the dashboard).
- Update `docs/architecture.md`: the parent area is a scope of the workshop, not a separate surface.
- Update the status table in `docs/product.md`.

## 7. Implementation order

1. Auth refactor: extract `verify_clerk_session`, rebuild parent deps on it (tests green).
2. `WorkshopScope` resolver + unit test.
3. Workshop server swap: new auth dependency → scope, operator path live, non-operator 403; delete secret login + config; update workshop tests.
4. Frontend: ClerkJS in `base.html`, new `login.html`, sign-out button, HTMX 401 handler.
5. Cleanup + docs: `render.yaml`, `.env` examples, setup doc, architecture + product docs.
