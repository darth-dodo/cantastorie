# Tasks

All R2 tests use moto with two buckets (public and pending), in the style of `tests/pipeline/test_publish.py`. Each test is written first and seen failing before the code that makes it pass.

## 1. Snapshot on every manifest write

- [x] 1.1 Test first in `tests/pipeline/test_snapshots.py`: the key layout maps `published/it/manifest.json` and `published/families/{token}/it/manifest.json` to distinct `snapshots/manifests/latest/...` and `snapshots/manifests/history/.../{timestamp}Z-{md5}.json` keys; verify with `uv run pytest tests/pipeline/test_snapshots.py`
- [x] 1.2 Test first: publishing a story leaves a latest copy and one history copy in the pending bucket, both byte-identical to the live manifest, and nothing under `snapshots/` in the public bucket
- [x] 1.3 Test first: re-publishing an unchanged story adds no history copy
- [x] 1.4 Test first: a family overlay publish snapshots under the family manifest's own path
- [x] 1.5 Test first: unpublish, `publish_prompts` and `scripts/repair_manifests.py` each leave a snapshot of the bytes they wrote (extend `tests/pipeline/test_prompts.py` and `tests/test_repair_manifests.py`)
- [x] 1.6 Test first: when the snapshot PUT raises, publish still returns its normal result, and `sentry_sdk.capture_exception` is called once with the manifest key and the family token hashed (no raw token in the event)
- [x] 1.7 Implement `src/pipeline/snapshots.py` (key layout, `write_snapshot`) and add the required keyword `snapshot_bucket` to `_write_manifest`; pass `settings.r2_pending_bucket` from every caller; verify 1.1 to 1.6 pass and `make typecheck` is green
- [x] 1.8 Test: the R2 bucket audit still passes on a moto bucket after publish and unpublish with snapshots on (extend `tests/pipeline/test_audit.py`)

## 2. Seed and list

- [x] 2.1 Test first: `snapshot-manifests --dry-run` lists every shared and family manifest and writes nothing; without `--dry-run` each gets a latest and a history copy and the count is printed (extend `tests/pipeline/test_cli.py`)
- [x] 2.2 Test first: `restore-manifest <key> --list` prints the latest copy and the history copies newest first with time and story count
- [x] 2.3 Implement `seed_snapshots`, `list_snapshots` and the two CLI commands; verify 2.1 and 2.2 pass

## 3. Restore with the review gate intact

- [x] 3.1 Test first: a restore without `--yes` prints added, removed and dropped story ids and leaves the live manifest byte-identical
- [x] 3.2 Test first: a snapshot entry whose `story.json` is gone (unpublished since) is dropped and reported, and the restored manifest does not list it
- [x] 3.3 Test first: in a family manifest snapshot, an entry pointing at the shared shelf or another family is dropped, and the restored manifest passes `audit_published_bucket`
- [x] 3.4 Test first: a confirmed restore writes exactly one public-bucket object (the manifest) and no story asset
- [x] 3.5 Test first: a confirmed restore takes a history copy of the live manifest first; restoring that copy returns the live manifest to its exact prior bytes; if that copy fails, the restore aborts and the live manifest is unchanged
- [x] 3.6 Test first: a restore whose `IfMatch` loses to a concurrent publish retries against the fresh manifest (reuse the race helper in `tests/pipeline/test_publish.py`)
- [x] 3.7 Test first: restoring the latest copy of a deleted manifest recreates it with the surviving entries
- [x] 3.8 Implement `restore_manifest` in `src/pipeline/snapshots.py` (lane check through `_check_manifest_url`, `story.json` existence, write through `_write_manifest`) and the `restore-manifest` CLI options `--from`, `--latest`, `--yes`; verify 3.1 to 3.7 pass

## 4. Docs and runbook

- [x] 4.1 Add "Recovering a manifest" to `docs/setup.md`: R2 has no object versioning (source link), the snapshot layout, the lifecycle rule command (`wrangler r2 bucket lifecycle add cantastorie-pending manifest-history snapshots/manifests/history/ --expire-days 30 -J eu`) and how to verify it (`wrangler r2 bucket lifecycle list cantastorie-pending -J eu`), the one-time seed, list, dry run, restore, undo, and "run `audit` after a restore"; verify every command in it runs as written against moto or with `--help`
- [x] 4.2 Update the manifest write path in `docs/system-overview.md` (every write also snapshots to the pending bucket) and close M13 in `docs/audits/release-readiness.md` with a link to the runbook

## 5. Verify

- [x] 5.1 `make check`, `uv run pytest` (coverage at or above the floor) and `npx vitest run` are green
- [x] 5.2 Review-gate check for the PR's "How verified": the tests in 3.2, 3.3 and 3.4 show a restore cannot list a story without a published `story.json` in its own lane and writes no asset

## Workflow follow-up

- Archive the change with `openspec-archive-change` in the build PR.
- After deploy, the owner applies the lifecycle rule, runs `snapshot-manifests` once, rehearses a restore of one manifest (dry run, `--yes`, then undo) and pastes the output into the runbook.
