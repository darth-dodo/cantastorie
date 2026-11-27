# manifest-recovery Specification

## Purpose
Lets the owner recover any shelf manifest (shared or a family's) after a bad write or a delete, from copies kept in the private bucket, without ever letting a restore list a story that a parent has not approved.

## Requirements

### Requirement: Every manifest write leaves a snapshot

Whenever the app writes a shelf manifest, it SHALL write a copy of exactly the bytes it wrote into the private pending bucket: one dated history copy and one latest copy, both keyed by the manifest's own path. A write that is skipped because the bytes are unchanged SHALL NOT add a history copy.

#### Scenario: Publishing a story snapshots the new manifest
- **WHEN** a story is published and its language manifest is rewritten
- **THEN** the pending bucket holds a history copy and a latest copy of that manifest, each byte-identical to the live manifest

#### Scenario: A family manifest is snapshotted under its own path
- **WHEN** a family overlay manifest `published/families/{token}/{lang}/manifest.json` is written
- **THEN** its snapshots are keyed by that path, separate from the shared shelf's snapshots

#### Scenario: An unchanged manifest adds no history
- **WHEN** a story is re-published and the manifest bytes are unchanged
- **THEN** no new history copy is written

#### Scenario: Every writer snapshots
- **WHEN** a manifest is written by publish, unpublish, publish-prompts or the repair script
- **THEN** a snapshot of the written bytes exists for each of them

### Requirement: A snapshot failure never fails the manifest write

When the manifest write succeeds but writing its snapshot fails, the operation SHALL still succeed, and the failure SHALL be reported to error monitoring with the manifest key, never the family token in clear.

#### Scenario: Snapshot bucket unavailable
- **WHEN** the manifest PUT succeeds and the snapshot PUT raises
- **THEN** the publish returns its normal result and one error event names the manifest key with the family token hashed

### Requirement: Snapshots stay private

Snapshots SHALL live only in the private pending bucket, under a `snapshots/` prefix kept apart from `pending/`, and SHALL never be written to the public bucket.

#### Scenario: Nothing lands in the public bucket
- **WHEN** any manifest is written and snapshotted
- **THEN** the public bucket contains no key under `snapshots/`, and the R2 bucket audit still passes

### Requirement: History expires, the latest copy does not

History copies SHALL expire after a fixed retention (default 30 days) set by a lifecycle rule on the pending bucket. The latest copy of each manifest SHALL be kept until it is replaced by a newer write, and the copy it replaces SHALL be kept as a non-expiring previous copy, restorable like any snapshot.

#### Scenario: A manifest unchanged for longer than the retention is still recoverable
- **WHEN** a manifest was last written more than the retention ago and its history copies have expired
- **THEN** its latest copy is still present and restorable

### Requirement: The owner can seed snapshots of every live manifest

An operator command SHALL copy every live manifest in the public bucket, shared and family, into the snapshot area. A dry run SHALL list what it would copy and write nothing.

#### Scenario: Seeding manifests that predate snapshots
- **WHEN** the seeding command runs against a bucket whose manifests have no snapshots
- **THEN** every live manifest has a latest copy and one history copy, and the command reports how many it copied

### Requirement: The owner can list a manifest's snapshots

An operator command SHALL list the snapshots held for one manifest key, newest first, with each one's time and story count.

#### Scenario: Listing snapshots
- **WHEN** the owner lists snapshots for `published/it/manifest.json`
- **THEN** each history copy and the latest copy are shown newest first with their time and number of stories

### Requirement: A restore is a dry run unless confirmed

Restoring a snapshot SHALL by default only print what would change: the story ids it would add to and remove from the live manifest, and any entries it would drop. It SHALL write only when the owner confirms with `--yes`.

#### Scenario: Restore without confirmation
- **WHEN** the owner restores a snapshot without `--yes`
- **THEN** the added and removed story ids are printed and the live manifest is unchanged

### Requirement: A restore never lists an unreviewed or removed story

A restore SHALL keep a snapshot entry only when that story's `story.json` exists under the same lane's `stories/` directory, its story URL is exactly that `story.json` under the lane's public base, its cover URL sits in that story's directory, and neither URL has a `..` segment. Every other entry SHALL be dropped and reported. A restore SHALL NOT write or copy any story asset.

#### Scenario: A story unpublished since the snapshot stays gone
- **WHEN** a snapshot lists a story whose directory unpublish has since deleted
- **THEN** the restored manifest does not list it and the restore reports it as dropped

#### Scenario: A cross-lane entry is refused
- **WHEN** a family manifest snapshot holds an entry pointing at the shared shelf or another family
- **THEN** that entry is dropped and reported, and the restored manifest passes the R2 bucket audit

#### Scenario: A restore writes only the manifest
- **WHEN** a confirmed restore completes
- **THEN** the only object written in the public bucket is that manifest

### Requirement: A restore is safe under concurrency and can be undone

A confirmed restore SHALL write through the same conditional write as publish and SHALL fail closed: when the live manifest changed between the restore's plan and its write, it SHALL write nothing and report that the manifest changed since planned. It SHALL first take a history copy of the live manifest it replaces (even one no app write produced) and SHALL leave a snapshot of what it wrote.

#### Scenario: A concurrent publish during a restore
- **WHEN** a publish changes the live manifest after the restore planned and before it wrote
- **THEN** the restore writes nothing, the concurrent publish stays listed, and the owner is told to rerun the dry run

#### Scenario: Undoing a restore
- **WHEN** the owner restores snapshot A and then restores the history copy the restore took of the manifest it replaced
- **THEN** the live manifest is byte-identical to what it was before the first restore

#### Scenario: Restoring a deleted manifest
- **WHEN** the live manifest no longer exists and the owner confirms a restore of its latest copy
- **THEN** the manifest is recreated with the latest copy's surviving entries

### Requirement: A restore re-checks stories at write time and skips a no-op

Just before the write, a restore SHALL check each kept entry's `story.json` again and drop any that vanished. A restore that would change nothing SHALL write nothing, take no backup and offer no undo.

#### Scenario: A story removed between plan and write
- **WHEN** a kept entry's `story.json` is deleted after the plan and before the write
- **THEN** the restored manifest does not list it and the restore reports it as dropped

#### Scenario: Restoring a copy equal to the live manifest
- **WHEN** the owner confirms a restore whose result equals the live manifest
- **THEN** nothing is written and no backup copy is taken
