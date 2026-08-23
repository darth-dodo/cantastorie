# Story CRUD — Published-Story Management

**Date:** 2026-08-23 · **Status:** Draft for review · **Phase:** 2 groundwork

---

## Summary

Parents and the operator can see and hard-delete published stories:

- **Parent** (`/parent`): lists only their own family's published packs; each row offers one destructive **Delete**.
- **Operator** (`/workshop/library`): lists everything published on R2 — all families' packs and bundled launch stories — grouped by language, with orphan story directories flagged; each row offers the same Delete.

Delete means gone forever: the manifest entry is removed and all published assets are deleted from R2. There is no hide/unpublish toggle and no content editing.

## Decisions Settled During Brainstorming

| Question | Decision |
|---|---|
| Which surfaces? | Both parent dashboard and workshop library |
| Parent delete semantics | One destructive action — shelf removal + R2 asset deletion; no reversible hide step |
| Operator scope | Deletes anything live on R2, including bundled launch stories |
| Post-generation editing | Out of scope; regeneration happens through existing run flows |

## Non-Goals

- Editing story text, images, or narration after generation
- Touching staged/pending run flows (`/workshop` review queue unchanged)
- Family-overlay manifests (Phase 2) — this feature works against today's shared manifests
- Kill switch, language tabs, or any other planned dashboard furniture

## As-Built Grounding

- `unpublish_story()` in `src/pipeline/publish.py` already performs the full destructive delete: it scans every `published/{lang}/manifest.json`, removes the story's entry, and bulk-deletes `published/stories/{story-id}/`. This feature adds **no pipeline changes**.
- Family overlay manifests do not exist yet; every published story lives in a shared per-language manifest. Consequence, accepted and documented: deleting a pack removes it from every family's shelf until overlays ship.
- Ownership is derivable: `RunStore.list_runs(family_token=…)` yields each run's `story_ids`; stories from `"approved"` runs belong to that family.

## Design

### Components

| Unit | Location | Responsibility |
|---|---|---|
| `list_published_stories()` | `src/pipeline/publish.py` | One paginated listing of `published/*/manifest.json` (via existing `_load_manifest`); returns rows `{id, title, language, cover}` |
| Orphan flagging | reuses `_find_orphan_story_dirs` | Library view marks `published/stories/{id}/` directories no manifest lists |
| Ownership check | helper in `src/api/routes/parent.py` | `story_id ∈ ⋃ story_ids of family's "approved"` runs; else 404 before any deletion |
| Parent routes | `src/api/routes/parent.py` | `GET /parent/stories` → HTMX rows; `POST /parent/stories/{story_id}/delete` |
| Operator routes | `src/api/routes/workshop.py` | `GET /workshop/library`; `POST /workshop/stories/{story_id}/delete` (operator-only, no ownership gate) |
| Delete primitive | existing `unpublish_story()` | Manifest-entry removal + asset bulk-delete |

Templates follow the established Jinja2 + HTMX patterns: rows render as partials, delete buttons carry `hx-confirm`, and delete handlers return an empty `HTMLResponse` on `HX-Request` so HTMX drops the row (the pattern `delete_run` already uses). Workshop routes reuse the existing operator guard; parent routes reuse the Clerk family-scope resolution.

### Data Flow

1. Page load → one paginated S3 listing of manifests (+ intersection with the family's approved-run story_ids on `/parent`).
2. Rows render: cover thumbnail, title, language, Delete button with `hx-confirm`.
3. Confirm → POST delete → ownership/auth checks → `unpublish_story()` → empty response removes the row.

### Run Records After Delete

Run records are a durable history and are **not** modified by a published-story delete — an `"approved"` record may reference a story_id whose assets are gone. The implementation-planning step must confirm how `/parent` my-packs renders such runs (publish-state should derive from manifest presence at render time, never claim "on shelf" for a deleted story) and adjust templates if needed. No record schema changes in this slice.

### Error Handling

| Case | Behavior |
|---|---|
| Unknown or not-owned story_id | 404 raised before any deletion call |
| Non-operator hits workshop library/delete | 403 (existing guard) |
| Unauthenticated request | Redirect to sign-in (existing behavior) |
| Story already deleted | No-op success — absent manifest entry and zero keys are handled by `unpublish_story` |
| R2 failure mid-request | 500 surfaces; the row remains and can be retried |

### Testing Plan

pytest + moto, following `tests/workshop/test_routes.py` conventions (providers mocked, fake S3):

1. `list_published_stories` merges rows across two language manifests correctly.
2. Parent list shows only their family's approved stories.
3. Parent delete of another family's story → 404, nothing deleted on R2.
4. Parent delete of own story → manifest entry gone, asset keys gone.
5. Operator deletes a bundled launch story without ownership constraints.
6. Workshop routes reject non-operators (403).

## Docs Impact (implementation must include)

- `docs/product.md`: dashboard wording changes from "unpublish toggles" to a single destructive delete; note the shared-manifest boundary.
- `docs/system-overview.md`: add the new listing function and routes to the module map.

## Known Boundary

Shared per-language manifests mean any pack delete is visible to all families. When Phase 2 ships token-keyed overlay manifests, both surfaces keep their routes and swap the listing/deletion target — no redesign.
