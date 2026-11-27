# Proposal

## Why

A manifest (`published/{lang}/manifest.json`, or a family's `published/families/{token}/{lang}/manifest.json`) is the index of a whole shelf, and today a bad write or a delete empties that shelf with no undo (release-readiness audit item M13, Issue #185). Cloudflare R2 does not offer object versioning: its S3 API compatibility table lists `PutBucketVersioning` and `GetBucketVersioning` as unsupported, so the recovery path has to be built by the app.

## What Changes

- Every manifest write the app makes (publish, unpublish, publish-prompts, the repair script) also writes a copy of the bytes it just wrote into the private pending bucket: a dated history copy under `snapshots/manifests/history/` and a "latest" copy under `snapshots/manifests/latest/`.
- An R2 lifecycle rule on `cantastorie-pending` expires history copies after a fixed number of days (default 30). The "latest" copy never expires, so a manifest that has not changed for months is still recoverable.
- A new operator command, `snapshot-manifests`, copies every live manifest into the snapshot area. It seeds snapshots for manifests that existed before this change and serves as a manual backup before risky bucket work.
- A new operator command, `restore-manifest`, lists the snapshots of one manifest and restores a chosen one. It is a dry run unless `--yes` is passed, prints what the restore adds and removes compared with the live manifest, writes through the same optimistic-concurrency path as publish (so the replaced manifest is itself snapshotted and the restore can be undone), and refuses any entry whose story is not already published in the same lane.
- `docs/setup.md` gains a "Recovering a manifest" runbook: the lifecycle rule command, the seeding step, and a rehearsed restore of one manifest.

## Safety and review gate

This touches publishing. A restore writes only a manifest; it never writes, copies or moves a story's assets, and it lists an entry only when that story's `story.json` already exists under the same lane's `stories/` directory and its URLs stay inside that lane. Only publish writes story directories, and publish runs only after parent approval, so a restore cannot surface a story that was never reviewed. Unpublish deletes a story's directory, so a restore of an older snapshot drops, not revives, a story that was taken down. Snapshots live in the private bucket and are never readable by the player.

## Capabilities

### New Capabilities
- `manifest-recovery`: snapshots of every shelf manifest the app writes, their retention, and an operator restore that keeps the parent review gate intact.

### Modified Capabilities
None.

## Impact

- Code: `src/pipeline/publish.py` (`_write_manifest` gains a required snapshot target; its callers in `publish.py`, `src/pipeline/prompts.py` and `scripts/repair_manifests.py` pass the pending bucket), a new `src/pipeline/snapshots.py`, two commands in `src/pipeline/cli.py`.
- Storage: one extra small PUT per manifest write and one "latest" object per manifest in `cantastorie-pending`. No new bucket, service, secret or dependency; the existing R2 token already has read and write on both buckets.
- Bucket config: one lifecycle rule on `cantastorie-pending`, applied with `wrangler r2 bucket lifecycle add ... -J eu`.
- Docs: `docs/setup.md` runbook, `docs/system-overview.md` manifest write path.
