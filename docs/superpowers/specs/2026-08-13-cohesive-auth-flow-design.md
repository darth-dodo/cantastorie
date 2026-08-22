# Cohesive Superuser/Parent Auth Flow — Design

> Issue: **AI-430**. Approach A from brainstorming (2026-08-13). Builds on AI-426 (workshop behind Clerk) and AI-411 (/parent surface), both merged.

## Goal

Make the Clerk-gated experience cohesive across the two roles — **superuser** (operator, global authoring) and **parent** (family-scoped) — so that signing in always lands you somewhere useful, no door is a dead-end, and the two surfaces read as one product.

## Problem (as built today)

- **A false dead-end.** A signed-in non-operator at `/workshop` renders `coming_soon.html` — "You're signed in, but your family workshop isn't ready yet." That was true before AI-411, but `/parent` now exists. The page offers only a "Sign out" button and no link onward.
- **Two disconnected doors.** `/workshop` ("Workshop — the room behind the piazza") and `/parent` ("The parent area") are separate sign-in surfaces for the *same* Clerk account, with different branding and no cross-linking.
- **Drift from the settled design.** AI-426 framed the parent area as "a scope of the workshop"; AI-411 shipped a separate `/parent`. `coming_soon.html` is the vestige of that unfinished idea.

## Role model

- **Superuser** — `public_metadata.role == "operator"` (surfaced as the `role` JWT claim). Global authoring; home is `/workshop`. A superuser is **not** given a parent view in this cut — they are routed to the workshop, full stop.
- **Parent** — every other signed-in user. Family-scoped; home is `/parent`. Identity comes from the verified session; `family_token` is minted or linked at first sign-in (AI-410).

This is unchanged from AI-426/AI-411 — `WorkshopScope.is_operator` already encodes it. The design only changes *where each role is sent*, not who is what.

## Design

### 1. Role dispatch — one serving home per role, no loops

Each entry route resolves the caller's scope and applies a single rule: **serve if this is the caller's home, otherwise 303-redirect to their home.** Because each role has exactly one route that *serves* (never redirects), redirect loops are impossible.

| GET | unauthenticated | superuser (operator) | provisioned parent | signed-in, unprovisioned |
|-----|-----------------|----------------------|--------------------|--------------------------|
| `/workshop` | shared sign-in (200) | **dashboard** (serve) | 303 → `/parent` | 303 → `/parent` |
| `/parent`   | shared sign-in (200) | 303 → `/workshop` | **packs** (serve) | onboarding → provision → packs |

Loop check: an operator is served at `/workshop` and only ever redirected *toward* it; a parent is served at `/parent` and only ever redirected toward it; an unprovisioned parent redirected `/workshop → /parent` lands on onboarding, which serves (no further redirect). No cycle exists.

A single shared helper is the source of truth for "home":

```python
def home_path(scope: WorkshopScope) -> str:
    return "/workshop" if scope.is_operator else "/parent"
```

Both routers import it. Mutation/HTMX routes keep their existing behavior (operator-only routes still 403/404 a non-operator; parent routes still require a provisioned parent) — dispatch changes only the **page GET** entry points, not the security of the action routes.

### 2. One sign-in surface

Consolidate `workshop/login.html` and `parent/signin.html`, and the two base templates (`workshop/base.html`, `parent/base.html`), into a **single shared sign-in template + base** with one wordmark and one ClerkJS mount. Whichever door the user arrives at renders the identical sign-in; Clerk's `afterSignInUrl` returns them to that door and the §1 dispatch routes them home, so the entry door no longer matters.

The Clerk-containment guard test is updated to allow the shared sign-in location (a shared `auth/`-style template dir, or a shared partial under the existing dirs — implementer's call in the plan) while keeping every child-player template and JS Clerk-free.

### 3. Delete the dead-end

`src/templates/workshop/coming_soon.html` is removed, along with the `_coming_soon` helper and the non-operator branch in `dashboard` that rendered it. After §1 that branch is unreachable — a non-operator at `/workshop` is redirected before any 403 page is considered.

### 4. Cohesive failure handling

- **Disabled account** → 403 (from `verify_clerk_session`), unchanged, on every route.
- **Clerk unconfigured** → both `/workshop` and `/parent` answer 404 (the feature gate stays exactly as is).
- **Session lapse mid-HTMX (401)** → full-page reload → sign-in. The parent surface adopts the workshop's existing `htmx:responseError` → reload handler so both areas recover identically.
- **No redirect loops** — guaranteed structurally by §1.

## Components touched (guidance for the plan)

- `src/workshop/scope.py` — add `home_path(scope)` (or a small shared `src/api/routes/_auth_nav.py`; implementer's call).
- `src/api/routes/workshop.py` — `dashboard`: non-operator → `303 home_path` instead of `_coming_soon`; drop `_coming_soon`.
- `src/api/routes/parent.py` — `parent_home`: operator → `303 home_path`; keep unprovisioned → onboarding, provisioned → packs.
- `src/templates/` — consolidate the two sign-in pages + two bases into one shared sign-in + base; delete `workshop/coming_soon.html`.
- `src/static/js/` — parent pages get the same `htmx:responseError` 401→reload handler the workshop has.
- Tests — `tests/workshop/test_routes.py`, `tests/api/test_parent_pages.py`: the full dispatch matrix, `coming_soon` unreachable/removed, Clerk-unconfigured 404, and the Clerk-containment guard still green.

## Testing

Route tests asserting every cell of the §1 matrix: operator served at `/workshop`; parent at `/workshop` → 303 `/parent`; operator at `/parent` → 303 `/workshop`; parent served at `/parent`; unauth → sign-in on both; unprovisioned → onboarding. Plus: no template named `coming_soon` is rendered by any route; Clerk-unconfigured → 404 on both; the Clerk-containment guard passes against the consolidated sign-in.

## Out of scope

- Any superuser *parent* view (an operator managing their own family). Deferred; the role model leaves room for it.
- Restyling the dashboard or packs pages beyond the shared sign-in/base shell.
- Sign-up flow, operator sub-roles, family-scoped publishing changes (AI-412 territory).
- Any child-player change.

## Acceptance

- The §1 dispatch matrix holds under tests; no role can reach a dead-end.
- `coming_soon.html` is deleted and unreachable.
- One shared sign-in renders at both doors; the Clerk-containment guard is green.
- No redirect loops; disabled→403 and Clerk-unconfigured→404 invariants preserved.
