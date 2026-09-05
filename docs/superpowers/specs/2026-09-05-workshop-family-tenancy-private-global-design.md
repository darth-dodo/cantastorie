# Workshop Multi-Tenancy — Private (family) vs Global (operator) Stories

**Date:** 2026-09-05 · **Status:** Approved design, pre-implementation · **Phase:** 2 (finishes the ADR-003/005 overlay path)

---

## Summary

The workshop already has a tenancy boundary (`src/workshop/scope.py`): operators
publish to the **shared shelf** (global, every child), families are confined to
their own `family_token` partition and publish to a **family overlay** (private,
only that family's child). The overlay lane is *decided and wired into scope*
(`publish_target="overlay"`) but **not physically built** — `publish_story()`
ignores the target and always writes the shared shelf, and the player reads only
the shared manifest.

This slice finishes the overlay lane so that:

- **Global stories** = operator-authored, published to `published/{lang}/manifest.json`. Reaches every child. *(Works today — unchanged.)*
- **Private stories** = family-authored/approved, published to `published/families/{token}/{lang}/manifest.json`. Reaches only that family's child.
- **The two lanes never cross.** There is **no promotion** of a private story to global. `publish_target` is one-way and origin-fixed.
- **Operator moderation:** the operator superuser can **see and delete** any family's private stories (safety/moderation) via `/workshop/library`. Delete is destruction, not promotion.

## Decisions settled during brainstorming (2026-09-05)

| Question | Decision |
|---|---|
| Tenant unit | **Family** (reuse the existing `family_token` boundary) |
| Meaning of "global" | **Published-to-all-shelves** (the shared per-language manifest), not an authoring library |
| Promotion private→global | **None.** Private stays private. Global is operator-authored only. |
| Operator power over private | **See + delete** (moderation), never promote |
| Story ownership representation | Prefix-based (R2 path). `Story` model stays owner-less — **no `owner`/`visibility` field** |

## Non-goals

- Any promotion / elevation of family content to the global shelf.
- Multi-parent families; token rotation UI.
- `owner`/`visibility` field on the `Story` model, or a single filtered manifest.
- Changing the operator's global publish flow (`/workshop` approve → shared) — unchanged.
- Regenerate-with-cap; kill switch; language tabs.

---

## Current-state grounding (verified against `origin/main` @ 4fb9699)

- `src/workshop/scope.py` — `WorkshopScope(user_id, is_operator, store_token, publish_target)`. `resolve_scope()`: operator → `store_token="operator"`, `publish_target="shared"`; family → `store_token=family_token`, `publish_target="overlay"`. Tested by `tests/workshop/test_scope.py`. **This is the security boundary — keep it pure and exhaustively tested.**
- `src/pipeline/publish.py`:
  - `PUBLISHED_PREFIX = "published"`, `STAGED_PREFIX = "pending/staged"`.
  - `publish_story(story_id, settings, *, client=None) -> PublishResult` — reads `pending/staged/{id}/`, uploads to `published/stories/{id}/…` and `published/prompts/{lang}/…`, then rewrites the manifest via `_publish_manifest`. **No scope/target parameter today.**
  - `_load_manifest(client, bucket, language)` keys `published/{language}/manifest.json`.
  - `_publish_manifest(...)` writes `published/{language}/manifest.json` with optimistic-concurrency retry.
  - `unpublish_story(story_id, settings)` — removes the manifest entry and bulk-deletes assets. Used by operator (`workshop.py:252`) **and** parent (`parent.py:173`).
  - `list_published_stories(...)` — powers `/workshop/library` (all families) and parent listing.
- `src/api/routes/workshop.py:73` — operator approve calls `publish_story(story_id, get_settings())` (shared, correct for global).
- `src/api/routes/parent.py` — parent currently **only deletes** (`unpublish_story`). **Parent approve → overlay publish is NOT wired.**
- `src/static/js/main.js` — `fetchManifest(assetBase, fetchFn, lang)` fetches a single `${assetBase}/${lang}/manifest.json`. **No overlay merge.** Player reads `family_token` from IndexedDB elsewhere (see `auth.js` / family store) — the token is available client-side.
- `tests/pipeline/test_audit.py` — the audit mechanism that asserts no unapproved / cross-tenant asset is manifest-reachable. **Extend it, do not weaken it.**

---

## Design

### Tenancy model (reused, not invented)

| Lane | Who | `publish_target` | Asset prefix | Manifest key | Reaches |
|------|-----|------------------|--------------|--------------|---------|
| **Global** | operator | `"shared"` | `published/stories/{id}/` | `published/{lang}/manifest.json` | every child |
| **Private** | family | `"overlay"` | `published/families/{token}/stories/{id}/` | `published/families/{token}/{lang}/manifest.json` | only that family's child |

The `family_token` prefix **is** the boundary. Nothing rewrites `publish_target`.

### Seam 1 — honor `publish_target` in the publish step (`src/pipeline/publish.py`)

Parametrize the publish prefix + manifest key by the scope. Preferred shape:

- Introduce a small helper that resolves a **publish root** from the scope:
  - shared → `published`
  - overlay → `published/families/{store_token}`
- Thread the scope (or `store_token` + `publish_target`) into `publish_story()`, `_load_manifest()`, `_upsert_story()`, and `_publish_manifest()` so the prefix/manifest-key derive from the root. Keep the shared path **byte-identical** to today (regression guard: existing publish tests unchanged).
- Assets stay content-addressed by hash; overlay publish of the same bytes re-buys nothing.
- Validate `store_token` matches `^[0-9a-f]{32}$` before building an overlay prefix (never build a prefix from an empty/garbage token — raise instead).

Call sites:
- Operator approve (`workshop.py:73`): pass the operator scope → shared (unchanged behavior).
- **Wire parent approve → overlay publish** in `parent.py`: on a family approving a staged story, call `publish_story` with the family scope so it lands under `published/families/{token}/…`. (If an approve route does not yet exist on the parent side, add it following the existing operator approve + HTMX patterns.)

### Seam 2 — child player merges the overlay (`src/static/js/main.js`)

- After fetching the shared manifest, if a `family_token` is present in IndexedDB, also fetch `${assetBase}/families/${token}/${lang}/manifest.json` and **append** its `stories` to the shelf (dedupe by `id`; shared wins on collision — though lanes don't collide by design).
- No token → **zero** extra requests (shared only).
- Overlay fetch failure (404/network) → shared shelf still renders; **never block bedtime**. Log-silent.
- Anonymous, bucket-direct, no credentials, no cookies — the child stays Clerk-free (existing containment guard must stay green untouched).
- Prompts: the overlay manifest carries its own `prompts`; prefer shared prompts, fall back to overlay only if shared absent (overlay stories reuse the same per-language prompt set in practice).

### Seam 3 — audit learns the families prefix (`tests/pipeline/test_audit.py` + any audit helper)

Extend the invariant:
- A family asset (`published/families/{token}/stories/…`) may be listed **only** in that family's overlay manifest.
- A family overlay manifest may reference **only** that family's own assets (no shared, no other family).
- The shared manifest may reference **only** `published/stories/…` (no family assets).
- Zero unapproved assets reachable from any manifest, shared or overlay.
- Add a **cross-tenant negative test**: a family-A manifest pointing at a family-B (or shared) asset is an audit failure.

### Operator moderation (see + delete over overlays)

- `/workshop/library` already lists all published content. Extend `list_published_stories()` (or the library route) to also enumerate `published/families/*/{lang}/manifest.json`, tagging each row with its owning family so the operator can see private stories.
- Extend `unpublish_story()` to accept an overlay target (same scope/root parametrization as Seam 1) so the operator can delete a family's private story: remove the overlay manifest entry + bulk-delete `published/families/{token}/stories/{id}/`.
- **No "promote" affordance** anywhere in the UI.
- Family delete stays scoped to their own overlay (404 on another family's story — reuse the existing ownership check).

---

## Testing plan

pytest + moto (fake S3), following `tests/workshop/test_routes.py` and `tests/pipeline/test_audit.py` conventions (providers mocked):

1. **Shared unchanged:** operator approve still writes `published/stories/{id}/` + `published/{lang}/manifest.json`, byte-identical to today (existing publish tests must pass untouched).
2. **Overlay publish:** family approve writes `published/families/{token}/stories/{id}/` + `published/families/{token}/{lang}/manifest.json`; shared manifest untouched.
3. **Cross-tenant isolation:** family A cannot list/view/delete family B's overlay story (404); family A's manifest never references B's assets.
4. **Operator moderation:** operator can list all overlays and delete any family's private story; assets + manifest entry gone.
5. **Empty/garbage token:** overlay prefix construction rejects a non-`^[0-9a-f]{32}$` token.
6. **Audit:** a family manifest pointing at another family's / shared assets fails the audit; a clean bucket passes.

JS (Vitest/jsdom, Clerk/network mocked, following existing `main.js` tests):

7. Player merges overlay stories when `family_token` present.
8. Player renders shared-only when token absent (asserts **no** overlay fetch).
9. Overlay fetch error → shared shelf still renders (no throw).

Guards stay green **untouched**: child-player Clerk containment; `disabled→403`; existing shared-shelf audit.

`make check` (or the repo's lint+type+test aggregate) clean.

---

## Implementation order (TDD — red/green per seam)

1. **Publish root parametrization** (Seam 1, publish.py) — write failing overlay-publish test → thread scope/root → green; shared-path regression tests stay green.
2. **Parent approve → overlay publish** (parent.py route) — failing route test → wire approve → green.
3. **Operator moderation** over overlays (list + delete) — failing tests → extend `list_published_stories`/`unpublish_story` + library route → green.
4. **Audit extension** (test_audit.py + helper) — add cross-tenant negative test → extend audit → green.
5. **Player overlay merge** (main.js + Vitest) — failing Vitest → implement merge with graceful fallback → green.
6. **Docs**: update `docs/product.md` (private vs global reach), `docs/architecture.md` / `docs/system-overview.md` (overlay publish path + player merge), and note the moderation capability.

Each step lands independently; the child player changes only at step 5.

## Out of scope / accepted boundaries

- No promotion tooling of any kind.
- Overlay token rotation remains the documented manual procedure (ADR-003).
- Multi-parent families deferred.
