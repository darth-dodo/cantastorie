# Delete Story + Assets From the Workshop — Design

Date: 2026-08-16
Status: Draft for review
Worktree: `.worktrees/workshop-delete-story` (branch `feat/workshop-delete-story`, from origin/main)

## Problem

The workshop at `/workshop` already deletes in two shapes:

- **Whole run** (`POST /workshop/runs/{id}/delete`) — dashboard × and run-page "× Delete run". If the run was approved it unpublishes every story in it (R2 manifest + published assets), deletes pending staged assets, removes local `content/{id}` folders, then the run record. Blocked while queued/running.
- **Single staged story** (`POST /workshop/staged/{id}/delete`) — "× Delete this story" on the story review page, for staged/failed runs only.

Two gaps remain:

1. **A published (approved) story cannot be deleted individually.** The route returns 400 for `approved` records and `story.html` hides the control. The only way off the shelf is deleting the entire run, which removes *every* story in it. Approved runs also list no story links on the run page, so individual approved stories are unreachable through the UI at all.
2. **A rejected story cannot be deleted individually either** — `reject` keeps its pending assets (contradicting product.md's "Reject | Deletes the pending assets"), and the single-story route returns 400 for `rejected` records.

## Decision

**Extend the existing single-story delete route** to accept `approved` and `rejected` records, reusing the two already-tested publish-layer helpers `unpublish_story()` and `delete_staged_story()` plus the local `shutil.rmtree`. No new endpoints, no new surface, no new storage writes.

### Route change

`POST /workshop/staged/{story_id}/delete` (`delete_staged_story_route` in `src/api/routes/workshop.py`):

- Keep: 403 for non-operator, 404 unknown story, 400 for `queued`/`running`.
- Replace the allowed-state guard `record.state not in {"staged", "failed"}` with: allow `staged`, `failed`, `rejected`, `approved`.
- Behavior by state:
  - `staged` / `failed` — unchanged: `delete_staged_story` + local rmtree + remove story_id from record.
  - `rejected` — same as staged: `delete_staged_story` + rmtree + remove story_id. Nothing was published, so no `unpublish_story` call.
  - `approved` — `unpublish_story(story_id, settings)` (removes the manifest entry and deletes `published/stories/{id}/` assets), then `delete_staged_story` (pending bucket), then local rmtree, then remove story_id from record.
- Record after removal: stays at its state (history preserved), matching how staged deletion behaves today. An approved record that loses its last story remains as history with empty `story_ids`.

### Template changes

- `src/templates/workshop/story.html` — show the delete row for every settled state (drop the `record.state not in ["approved"]` exclusion):
  - `approved`: label **"× Remove from shelf"** (posting to the same `/workshop/staged/{id}/delete` route).
  - all other settled states: existing "× Delete this story".
  - Keeps the armed two-tap `data-delete-btn` pattern — no one-tap destructive buttons.
- `src/templates/workshop/_progress.html` — for `approved` runs, render story links (the same story pills as the staged review pills, but linking to `/workshop/staged/{story_id}?run={id}`) so operatored approved stories are reachable. Currently approved runs show no story links.

### Assets deleted for an approved story

1. Manifest entry + `published/stories/{story_id}/` keys — `unpublish_story()`.
2. `pending/staged/{story_id}/` keys — `delete_staged_story()`.
3. Local `content_dir/{story_id}` — `shutil.rmtree(..., ignore_errors=True)`.

## Out of scope

- Approach B (a "Published" section on the dashboard listing stories from manifests) — follow-up candidate.
- Parent-area unpublish toggles from product.md (a different surface).
- Cleanup of orphan `pending/staged/prompts/{lang}/` keys — each `delete_staged_story` only removes its own story prefix; prompts are shared per language and reused.
- `rejected` runs' own record-level handling beyond per-story delete.

## Testing

Extend `tests/workshop/test_routes.py` (existing harness with moto S3):

1. Approved story delete → manifest no longer lists the story, `published/stories/{id}/` keys gone, `pending/staged/{id}/` keys gone, local `content_dir/{id}` gone, record still exists in `approved` state with `story_ids` emptied.
2. Rejected story delete → pending staged assets gone, no publish bucket touched, record updated.
3. Approved run page → story links render (and carry `data-testid`).
4. Approved story page → shows the armed "Remove from shelf" control; live runs still hide it.
5. Existing tests for staged/failed delete, run delete, and guard rails remain green.

## Files touched

- `src/api/routes/workshop.py` (route guard + approved/rejected branches)
- `src/templates/workshop/story.html` (delete row for approved)
- `src/templates/workshop/_progress.html` (story links for approved runs)
- `tests/workshop/test_routes.py`
