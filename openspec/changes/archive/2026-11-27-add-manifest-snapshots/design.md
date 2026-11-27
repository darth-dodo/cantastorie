# Design

## Context

- Every manifest write goes through one helper, `_write_manifest` in `src/pipeline/publish.py`: load, mutate, skip when the bytes are unchanged (ETag equals the body MD5), else `put_object` with `IfMatch` on the ETag (no condition for a manifest that does not exist yet) and up to three retries on `PreconditionFailed`. Its callers are `_publish_manifest` and `unpublish_story` (`publish.py`), `publish_prompts` (`src/pipeline/prompts.py`) and `scripts/repair_manifests.py`. Nothing in the app deletes a manifest; deletes come from outside (wrangler, bucket cleanups).
- Manifests exist per lane: shared `published/{lang}/manifest.json` and family `published/families/{token}/{lang}/manifest.json`. `audit_published_bucket` and `_check_manifest_url` already define what a lane-safe entry is.
- R2 facts (Cloudflare R2 S3 API compatibility page, checked 26 Nov 2026): `PutBucketVersioning`, `GetBucketVersioning` and object lock are unsupported; `PutObject` conditional headers, `CopyObject` and `PutBucketLifecycleConfiguration` (expiry by prefix and age) are supported. Publish already copies across the two buckets with one token that has read and write on both.
- CI holds R2 secrets for the R2 Bucket Audit job, but that job runs only on push to `main`.

## Goals / Non-Goals

**Goals:**
- After any bad app write, or any outside delete, the owner restores a manifest with one command.
- No new service, secret, dependency or workflow.
- The restore keeps the parent review gate: it can only list stories already published in that lane.

**Non-Goals:**
- Backing up story assets, prompts or the pending bucket's run records and staged stories (the 1,390 `pending/` objects in the issue are a different kind of loss; noted as a follow-up question).
- Point-in-time recovery of the whole bucket.
- A UI for restores; this is an operator CLI.

## Decisions

**1. Snapshot after write, inside `_write_manifest`, over a scheduled snapshot job.**
After a successful PUT, the helper writes the same bytes to `snapshots/manifests/history/{manifest key}/{UTC timestamp}Z-{md5}.json` and `snapshots/manifests/latest/{manifest key}` in the pending bucket. One chokepoint covers every writer, history has one copy per real change (not one per day), and the latest copy always equals the last app-written state, so an outside delete loses nothing the app wrote. A nightly GitHub Actions snapshot would lose up to a day of publishes, add a workflow with R2 secrets on a schedule, and would still need the restore command. A snapshot before write (copy the old object) was rejected: it leaves the newest state unprotected until the next write.

**2. A required keyword `snapshot_bucket` on `_write_manifest`.**
Making it required (not defaulted) means mypy flags any writer that forgets it, including future ones. Callers pass `settings.r2_pending_bucket`.

**3. Snapshot failures are best-effort and loud.**
The manifest is already live when the snapshot PUT runs; failing the publish then would report an error for a story that is in fact on the shelf. So the helper catches the error, logs it and sends `sentry_sdk.capture_exception` with the manifest key, the family token replaced by `family_hash` (the logging integration has `event_level=None`, so a log line alone would not alert). The next write snapshots again.

**4. Retention by an R2 lifecycle rule, not app code.**
`wrangler r2 bucket lifecycle add cantastorie-pending manifest-history snapshots/manifests/history/ --expire-days 30 -J eu`. The `latest/` prefix has no rule. Pruning in app code was rejected as more code for a feature R2 provides. 30 days is the default; the owner may pick another number (question in the issue).

**5. `snapshots/` sits outside `pending/`.**
Cleanups of `pending/` (like the one that removed 1,390 objects) cannot reach snapshots by prefix.

**6. Restore reuses the audit's lane rules and writes through `_write_manifest`.**
`restore-manifest <key> [--from <history name> | --latest] [--yes]`, plus `--list`. It loads the chosen snapshot, keeps each entry only if `{lane root}/stories/{id}/story.json` exists and `_check_manifest_url` passes for its `story` and `cover` URLs, keeps the snapshot's `prompts` only if each prompt object exists, prints added, removed and dropped ids, and stops unless `--yes`. With `--yes` it first takes a history copy of the live manifest (aborting the restore if that copy fails, since this is an operator command and the copy is the undo), then calls `_write_manifest` with a `mutate` that replaces the content, so `IfMatch` and retries apply and the result is snapshotted like any write. A missing live manifest is recreated (the helper's create path).

**7. `snapshot-manifests` seeds and backs up.**
Lists every `*/manifest.json` under `published/` (both lanes) and writes latest and history copies. It runs once after deploy, and before any manual bucket work. `--dry-run` lists only.

**8. Code lives in a new `src/pipeline/snapshots.py`.**
Key layout, the snapshot write, list, seed and restore. `publish.py` imports only the write function, keeping the publish module from growing further.

## Risks / Trade-offs

- [Extra latency on every manifest write: one more small PUT] → Manifests are a few KB and written only at publish, unpublish and prompt time, never at story time.
- [An outside write (wrangler) that corrupts a manifest is not snapshotted] → The latest copy still holds the last app-written state, and the restore takes a history copy of whatever it replaces, so the corrupt state is kept too.
- [The latest copy can lag the live manifest if a snapshot PUT failed] → Sentry alert; the next write or a `snapshot-manifests` run closes the gap.
- [Snapshots of family manifests carry the family token in their keys] → The pending bucket already holds `pending/{token}/`; it is private, and the token is never logged in clear.
- [A restored entry whose assets are partly missing] → The existence check is on `story.json`; the CI audit on the next push to main checks every asset, and the runbook says to run `audit` after a restore.

## Migration Plan

1. Merge; Render deploys. New writes start producing snapshots.
2. Owner applies the lifecycle rule with wrangler (`-J eu`) and runs `uv run python -m src.pipeline.cli snapshot-manifests` once.
3. Owner rehearses a restore of one manifest following the runbook and records the output in `docs/setup.md`.
Rollback: revert the PR. Snapshots left in the pending bucket are inert; remove them with a prefix delete if wanted.
