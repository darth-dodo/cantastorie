# ADR-012: Durable Story Runs on Temporal

**Date**: 2026-11-28
**Status**: Proposed. Hosting decided by the owner on 2026-11-28: "Cloud" (Temporal Cloud, pay-as-you-go, `aws-eu-central-1` Frankfurt; see [Hosting options](#hosting-options)).
**Context**: The owner's goal of 2026-11-28, "use the factory and move the pipeline to temporal", routed by `factory:ask` as Gated because it changes a settled decision. The owner's goal is the yes to plan it; this ADR records the trade-off and supersedes part of two Accepted ADRs once it is Accepted.
**Decider(s)**: Project Owner
**Supersedes (on acceptance, in part)**: [ADR-001](ADR-001-technology-stack.md) "Plain Python pipeline, no graph or orchestration framework" for how a workshop run is *executed* (the step functions stay plain Python and Pydantic AI); [ADR-005](ADR-005-workshop-area.md) Option A's "in-process background runs, one at a time, resume on boot" (the workshop surfaces, R2 run records and the review gate stay).

---

## Summary

Each workshop story run becomes one **Temporal workflow** with **one activity per pipeline step** (write, safety, revise, narrate, illustrate, image safety, assemble, narrate prompts, stage). The activities call the existing step functions unchanged. A new **Render background worker** runs the activities; the web service only starts workflows and reads run state. The **content-addressed cache gains an R2 tier** in the private pending bucket, so a step that already ran on a container that has since been replaced is a lookup, not a second purchase. The workflow ends at **staged**: publishing stays a parent (or operator) HTTP action bound to the reviewed bytes, exactly as today.

The migration is behind a flag (`PIPELINE_ENGINE=inprocess|temporal`, default `inprocess`); the in-process path stays until the cutover is rehearsed, then is removed.

The host is **Temporal Cloud, pay-as-you-go (Developer support), in AWS Frankfurt (`aws-eu-central-1`)**, chosen by the owner on 2026-11-28 ("Cloud"), with payloads encrypted before they leave the worker. Expected cost: about **$7 to $10 a month** in total, almost all of it the Render worker. Self-hosting Temporal on Render costs about **$40 a month** and adds a database and an upgrade path to run. Self-hosting was considered and not chosen.

---

## Problem Statement

### The Challenge

A story run is several minutes of paid provider calls (text, two judges, narration, images and the vision judge). Today it runs inside the web process (`src/workshop/manager.py`): a FastAPI background task calls `generate_story` in a thread under one `asyncio.Lock`. The design assumed that "a restart re-buys nothing" because every step is checkpointed in the `ArtifactCache` (ADR-001, ADR-005). On Render that assumption does not hold:

1. **The checkpoint store is wiped by the event it is meant to survive.** `render.yaml` sets `CONTENT_DIR=/tmp/cantastorie-content`. Render's disk is ephemeral, so a deploy or restart deletes every checkpoint. `docs/setup.md` records the consequence and accepts it: "A resumed run after a deploy re-pays the API calls for every step that hadn't finished before the restart, its cache is gone."
2. **Rolling deploys can pay for one run twice.** During every zero-downtime deploy two instances are alive, both run `resume_on_boot()` and both can execute the same record. The ETag guard protects only the final save (`docs/setup.md`, "Rolling-deploy overlap", "Double generation cost").
3. **Shutdown does not stop the work.** Cancelling the boot task does not stop the `asyncio.to_thread` call underneath, so an outgoing instance can keep calling providers after it has shut down, unobserved (`docs/setup.md`, "Shutdown cancellation detaches rather than stops").
4. **Recovery is a reaper on a timer.** A run whose process died is found by `reap_stale()` only after `run_stale_after_seconds` (1,800 s) and is marked failed with `INTERRUPTED_NOTE`, or re-entered from scratch at boot. There is no retry per step: one transient failure the provider transport does not retry fails the whole run.
5. **One worker, one instance, by assumption.** Generation, the per-family cap and run bookkeeping are only correct with one web process and one instance, and nothing enforces it (Issue #184).
6. **Progress and failure location come from local folders.** The progress beads read checkpoint directories on the web container's disk (`_checkpointed_steps` in `src/api/routes/workshop.py`), so a failed run cannot say where it stopped (Issue #172, open PR #209) and a run on another process would show no progress at all.
7. **Run records have no second copy.** Records live only under `pending/` in R2 (Issue #199).

### Why This Matters

- Every re-bought step is real money for images and narration, and a deploy is the most common restart.
- Parents wait on these runs. A run that silently restarts from scratch, or is reaped after 30 minutes, is a broken promise in the parent area.
- Moving to more than one instance, or to batch generation for the launch library (Issue #158), is blocked on the same single-process assumptions.

### Success Criteria

- [ ] A worker restart or deploy in the middle of a run finishes the run without paying again for any step that had completed (verified against provider usage or LangSmith call counts in a rehearsal)
- [ ] Two web instances alive at once start at most one execution of a run
- [ ] A step that fails with a retryable error is retried on its own; a safety rejection is never retried
- [ ] The progress view and the failed screen name the real step, from state the web service can read without the worker's disk
- [ ] The child never meets an unreviewed story: the workflow ends at staged, and publishing still happens only through the approve routes with the reviewed-digest check
- [ ] The in-process path can be switched back on with one environment variable until it is removed

---

## Context

### Current State

- `generate_story` (`src/pipeline/generate.py`) runs `author_story` (write, safety gate, at most one revise, safety again), `narrate_pages` and `narrate_choice_labels`, `illustrate_safely` (illustrate, image safety, at most two redraws per image), `assemble_story`, the Italian `synthesize_utterances`, then `stage_story` to `pending/staged/{story-id}` in the private bucket.
- `run_step` in `src/pipeline/cache.py` is the checkpoint primitive: hash the inputs, return the cached bytes or produce and store them atomically.
- Provider calls retry at the transport (`src/pipeline/retry.py`): at most three sends, only on 429/502/503/504 and connect failures, never on a plain 500 or a read timeout, because the provider may already be billing.
- `RunRecord` (`src/workshop/records.py`) follows `queued -> running -> staged -> approved | rejected`, with `failed -> queued`. Saves are conditional on the record's ETag. Parents have a one-active-run cap and a daily cap; there is a global daily cap of 40 runs (`global_daily_run_cap`).
- Approve binds to the reviewed bytes (`reviewed_digest`, `publish_story(..., expected_digest=...)`) for the family lane; the operator approve publishes to the shared shelf.
- Hosting is one Render Starter web service in Frankfurt (`render.yaml`), deployed by CI's deploy hook.

### Requirements

- Keep the step functions, the cache keys, the safety gates and their bounds exactly as they are.
- Keep the review gate exactly as it is: no workflow path may publish.
- Keep previews read-only: no worker, no Temporal credentials in previews.
- No child data anywhere near the engine; workflow history must not hold a family token or a parent's premise in clear.
- One new runtime dependency at most (`temporalio`), added at build time by the spike task, not in this planning change.
- A rollback that is one environment variable until the old path is removed.

---

## Options Considered

### Option A: Keep the in-process runs and patch the gaps

**Description**: Keep ADR-005's model and fix what the evidence shows: mirror the `ArtifactCache` to the private bucket, add a conditional "claim" write so only one instance executes a run, record the failed step (#172), and set `--workers 1` and `numInstances: 1` (#184).

**Pros**:
- No new service, dependency or vendor; $0 a month extra
- Smallest change; the code stays a `for` loop
- The R2 cache tier alone removes most of the re-buy cost

**Cons**:
- Builds a small workflow engine by hand: claims with leases, heartbeats, a reaper, per-step retries, each with its own tests and edge cases
- Generation still shares the web process: a web deploy still interrupts every live run, and a thread still cannot be stopped on shutdown
- Scaling the web service stays blocked on the single-process assumption

**Risks**:
- Lease and reaper timing bugs are the kind that show up only in production deploys

**Estimated Effort**: Medium, about 2 days (unverified).

### Option B: A simple durable queue (RQ or arq with Redis)

**Description**: The web service enqueues a job per run on Redis (Render Key Value); a background worker runs `generate_story` from the queue.

**Pros**:
- Moves generation out of the web process; web deploys no longer interrupt runs
- Small libraries with a familiar model; jobs retry on failure

**Cons**:
- The unit of retry is the whole job, not a step: a retried job starts again from `write`, so it is only cheap if the cache is durable, which is Option A's R2 tier anyway
- At-least-once delivery with no step history: a worker killed mid-job leaves the job to a timeout and a full re-run, so double execution still needs a claim write
- Adds Redis, a second piece of standing state, that holds nothing the R2 run record does not already hold
- arq is asyncio-native but the pipeline is synchronous; RQ forks per job, which loses the warm clients

**Risks**:
- Render Key Value's free plan does not persist; a paid plan is needed for the queue to survive a restart

**Estimated Effort**: Medium, about 2 to 3 days (unverified). **Cost**: worker $7 plus Key Value $10 a month.

### Option C: Temporal workflows, one activity per step (chosen)

**Description**: A run is a workflow whose id is derived from the run id. Each pipeline step is an activity with its own timeout, heartbeat and retry policy. A Render background worker hosts the activities; the web service starts workflows and reads the run record. The cache gets an R2 tier so an activity retried on a new container finds its earlier artifacts.

**Pros**:
- Durable by construction: completed activities are recorded in the workflow history, so a worker restart resumes at the next step, not at `write`
- Duplicate starts with the same workflow id are rejected by the server, which removes the rolling-deploy double run without a hand-written claim
- Per-step retry policies, with safety rejections and billing-risky failures marked non-retryable
- Generation leaves the web process: web deploys never touch a live run, and the web can scale (most of #184)
- A visible history per run (the Temporal UI) for debugging, and batch generation (#158) becomes many workflow starts

**Cons**:
- A new vendor or a self-hosted service, and a new dependency
- Two more secrets in Cloud (API key, payload key)
- Workflow code has determinism rules; tests need the Temporal test server binary in CI
- LangSmith traces split per activity instead of one trace per run

**Risks**:
- Over-engineering for a household-scale workload; mitigated by keeping the workflow thin and the steps unchanged
- Vendor dependency for a core path; mitigated by the flag and by Temporal being open source (self-hosting stays possible)

**Estimated Effort**: High, about 4 days across five or six PRs (unverified until the spike).

### Option D: A graph framework (LangGraph or Pydantic Graph) with a checkpointer

Already rejected in ADR-001 for the pipeline's shape. A graph checkpointer still runs in the web process and still needs a durable store and a worker, so it solves less than Option C with the same new dependency. Not considered further.

### Option E: Render Workflows (rejected by the owner on 2026-11-28: "Temporal")

**Description**: Render's own durable runs (GA 2026-09-01). A flow is a plain Python function whose `ctx.step(...)` calls are checkpointed; if an instance fails, Render replays the flow on a new instance and reuses the results of completed steps. The web service starts a run with the `render` SDK's `start_flow` (with an `idempotency_key`) and reads it with `get_flow_run`. Declared in `render.yaml` as `type: workflow`.

**What it would have offered**:
- No new vendor and no always-on worker: per-second flex compute plus $0.25 per GB of retained state, about $2 to $3 a month at about 100 runs
- Each run stays on the workflow version it started on, even when a new version deploys ("All attempts of a flow run use the workflow version the run started on")

**Why it was rejected**:
- Starting a run needs an account-wide `RENDER_API_KEY` on the web service, a credential that can also deploy, change or delete every service in the account
- Step retries take only a count, a delay and a backoff (`Retry(max_retries, wait_duration_ms, backoff_scaling)`); there is no way to mark an error non-retryable, so a safety rejection or a provider read timeout would be retried like any other failure, unless every step catches and re-wraps its own errors
- A Frankfurt workflow service is unconfirmed: the docs name no region for workflows except an Oregon exception for flows
- It is new (GA 2026-09-01), and the docs do not describe what happens to a step in flight during a deploy
- Not a differentiator: steps run one at a time on one instance and an interrupted step re-runs in full, and step outputs are capped at 4 MB with state kept 30 days, so the R2 cache tier is needed under Render Workflows too

**Estimated Effort**: Similar to Option C. **Cost**: about $2 to $3 a month.

---

## Hosting options

Prices checked on 2026-11-28 from [temporal.io/pricing](https://temporal.io/pricing), [docs.temporal.io/cloud/pricing](https://docs.temporal.io/cloud/pricing), [docs.temporal.io/cloud/actions](https://docs.temporal.io/cloud/actions), [docs.temporal.io/cloud/regions](https://docs.temporal.io/cloud/regions), [render.com/pricing](https://render.com/pricing) and [render.com/docs/free](https://render.com/docs/free). Every option needs the Render background worker (Starter, $7 a month, 512 MB), because the worker runs the pipeline.

**Usage assumption (unverified, measured in the spike)**: about 40 billable Actions per run (one workflow start, about 12 activity starts including a revise and a redraw, throttled heartbeats during illustrate and narrate). Progress is read from the R2 run record, not by Temporal queries, because every query a worker answers is a billable Action and the progress view polls every 2 seconds. Realistic volume is about 100 runs a month; the global cap allows at most about 1,200.

| Option | Monthly cost | What it is | Pros | Cons |
|---|---|---|---|---|
| **Temporal Cloud, pay-as-you-go** (Developer support), `aws-eu-central-1` Frankfurt | **About $7.25** at 100 runs (worker $7, Actions 4,000 x $50 per million = $0.20, support 10% = $0.02, storage under $0.10). **About $10** at the cap (48,000 Actions = $2.40 + $0.24). New accounts get $150 credit for 90 days. | Managed Temporal; no base fee since September 2026 | Nothing to run or upgrade; Frankfurt is Render's region; usage cost is cents at this volume; UI included | A new vendor holds run metadata (ids, step names, timings); needs an API key and a payload encryption key; Developer support only (no response-time commitment) |
| **Self-hosted Temporal on Render** | **About $40** (worker $7 + Temporal server as a private service, Standard 2 GB $25 + Render Postgres Basic-256mb $6 + about 5 GB storage at $0.30 = $1.50). From about $21.50 if the server fits Starter 512 MB (unverified, likely too small for frontend, history, matching and worker services in one process); about $59 with Postgres Basic-1gb $19 and a $7 UI service. | `temporalio/server` image as a Render private service on Render Postgres | No new vendor; data stays in our Render account; private network only | Four times the cost; we own Temporal upgrades and schema migrations, Postgres sizing and backups; Render's free Postgres expires after 30 days, so there is no free path; no UI unless we run and protect one |
| **Temporal dev server** (`temporal server start-dev`) | $0 | In-memory, or SQLite with `--db-filename` | Ideal for local development and CI tests; one binary | Temporal: "The development server is not intended for production use." Not an option for production; used for `make dev` and tests in every option |

**Decision (owner, 2026-11-28: "Cloud"): Temporal Cloud, pay-as-you-go, `aws-eu-central-1`**, with a payload codec (AES-GCM through the existing `cryptography` dependency) so workflow inputs and results are ciphertext to Temporal, and workflow ids that carry only the random run id. It is the cheapest option that is safe for production, it adds no infrastructure to run, and it keeps self-hosting open because the SDK code is the same. Self-hosting on Render was considered and not chosen: it keeps every byte in our own Render account, but costs about $33 a month more and adds the upgrade, migration and backup work.

---

## Comparison Matrix

| Criterion | Weight | A: Patch in-process | B: RQ or arq + Redis | C: Temporal (Cloud) |
|-----------|--------|---------------------|----------------------|---------------------|
| Durability across deploys and restarts | 25% | 3 | 3 | 5 |
| No double spend (overlap, retries) | 20% | 3 | 3 | 5 |
| Operational burden | 20% | 4 | 3 | 4 |
| Monthly cost | 15% | 5 | 3 | 4 |
| Fit with the settled, minimal stack | 10% | 5 | 3 | 2 |
| Migration risk | 10% | 4 | 3 | 3 |
| **Weighted score** | | **3.8** | **3.0** | **4.15** |

Scores 1 to 5, higher is better. Self-hosted Temporal scores the same as C on durability and double spend, and lower on cost (2) and operational burden (2), for **3.45**.

---

## Decision

### Chosen Option: C, Temporal workflows with one activity per step

**Hosting**: Temporal Cloud, pay-as-you-go, `aws-eu-central-1` Frankfurt (owner, 2026-11-28: "Cloud"). Self-hosted Temporal on Render was considered and not chosen.

**Rationale**: The failures in the evidence (lost checkpoints on deploy, a second execution during the overlap, threads that cannot be stopped, a timer-based reaper) are the problems a durable workflow engine exists to solve. Option A fixes them by building a small engine by hand; Option B moves the work to a queue but keeps whole-run retries. Option C keeps the step functions as they are and gives each step its own record, retry policy and timeout.

**Key Factors**:
1. Duplicate workflow ids are rejected by the server, which is the run-level lock `docs/setup.md` says is missing.
2. Activities run in a separate worker, so web deploys stop interrupting runs and the web can scale.
3. At this volume Temporal Cloud costs cents beyond the worker.

**Trade-offs Accepted**:
- One new dependency (`temporalio`) and, with Cloud, one new vendor and two secrets.
- The ADR-001 line "no orchestration framework" no longer describes how runs execute; the steps themselves stay plain Python.
- Intermediate artifacts are stored in the private pending bucket (an R2 lifecycle rule expires them).

---

## What happens to each part

- **Filesystem checkpoints**: stay as the first cache tier on the worker's local disk. A miss checks the new R2 tier (`pending/cache/{story-id}/{step}/{key}`) before calling a provider, and every produced artifact is written to both. Activities pass references (story id, cache keys), never image or audio bytes, so payloads stay small and content never enters workflow history.
- **Run records**: stay in the private bucket and stay the source of truth for review state (`staged`, `reviewed`, `reviewed_digest`, `approved`, `rejected`). Activities write `running`, the current step, the completed steps, `staged` and `failed` with the failed step (#172). Records gain an `engine` field; records without it are in-process.
- **The reaper and boot resume**: apply only to in-process records during the migration and are removed with the old path. For Temporal records the web service reconciles a record against the workflow's status (a describe call, not a billable Action) when the workflow closed without updating it.
- **The workshop progress UI**: reads the current and completed steps from the run record, not from the container's folders. The bead names do not change.
- **The parent review gate**: unchanged. The workflow ends when the story is staged. No activity can publish; the worker does not register any publish function. Approve and reject stay HTTP routes, the family lane keeps the reviewed-digest check, and the operator lane keeps its shared-shelf publish.
- **Safety gates**: unchanged and inside their activities. A safety, image safety or content-rules rejection is a non-retryable failure, so Temporal never re-rolls a judge until it passes. The bounded revise and redraw loops stay as they are.

## Consequences

### Positive Outcomes

- A deploy no longer re-buys completed steps or runs a story twice
- Per-step retries for transient failures, with no change to the transport's billing-safe rules
- The failed screen names the real step (#172) and the progress view works for runs on another process
- Web instances can scale; the remaining single-instance issue in #184 is the cap check's read-then-write
- Batch generation (#158) becomes a loop of workflow starts with resume for free

### Negative Outcomes

- Two services to deploy instead of one, and a new failure mode: runs wait while the worker is down
- The Temporal test server binary is downloaded in CI for workflow tests
- With Cloud, run metadata (random run ids, step names, timings, error types) lives with a vendor in Frankfurt

### Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Temporal retries a call the provider already billed | Activity retry policy marks read timeouts and plain 500s non-retryable, matching `retry.py`; the cache makes a retry after a stored result free |
| A judge is retried until it passes | Safety, image safety and content-rules rejections are non-retryable application errors; a test asserts one judge call per verdict |
| A parent's premise or a family token reaches workflow history | Workflow input is the run id and an encrypted store reference; a payload codec encrypts every payload; a test asserts no plaintext premise or token in the recorded history |
| The worker is down and runs pile up | Runs stay `queued` with a "waiting for the workshop" note; Render restarts the worker; Sentry reports worker crashes |
| Workflow determinism bugs | The workflow only sequences activities and loops on their results; replay tests run in CI |
| Vendor outage or price change | The flag returns to the in-process path until it is removed; afterwards, self-hosting uses the same code |

---

## Implementation Plan

Planned as the OpenSpec change `move-pipeline-to-temporal`:

1. **Spike behind the flag**: add `temporalio`, a Temporal client from settings, the payload codec, and one workflow with one activity, run against the dev server in tests. Measure Actions per run.
2. **The workflow**: one activity per step wrapping the existing functions, the R2 cache tier, retry policies, record updates from activities.
3. **The worker service**: `python -m src.workshop.worker` as a Render background worker in `render.yaml`, excluded from previews, with its env vars.
4. **The workshop reads workflow state**: the progress view and failed screen read the run record; submit, retry and run again start workflows when the flag is on.
5. **Cutover**: rehearse a deploy in the middle of a run with the flag on, compare provider call counts, then set `PIPELINE_ENGINE=temporal` in production.
6. **Remove the old path**: the background task, boot resume, reaper and the setup.md notes they needed; update `docs/architecture.md`, `docs/system-overview.md`, `docs/setup.md`, and mark ADR-001 and ADR-005 as superseded in part.

**Rollback plan**: until step 6, set `PIPELINE_ENGINE=inprocess`. New runs take the in-process path; Temporal runs already started finish on the worker, which stays deployed until none are open. After step 6, rollback is a revert of the removal PR.

---

## Validation

- Workflow and activity tests against the Temporal time-skipping test server; replay tests for determinism
- A test that the worker registers no publish function, and that a completed workflow leaves the record `staged` and unreviewed
- A test that a safety rejection fails the run without a retry
- A deploy rehearsal on production with the flag on: restart the worker during `illustrate` and compare provider call counts with a clean run
- After cutover, a month of Temporal Cloud usage checked against the estimate in this ADR

---

## Related Decisions

- [ADR-001](ADR-001-technology-stack.md): the plain-Python pipeline and filesystem checkpoints; superseded in part on acceptance
- [ADR-005](ADR-005-workshop-area.md): the workshop and in-process runs; its Option C (a worker service with a queue) was rejected for volume, and this ADR revisits it with the evidence above; superseded in part on acceptance
- [ADR-007](ADR-007-langsmith-observability.md): tracing continues per activity, grouped by run id
- [ADR-009](ADR-009-sentry-error-monitoring.md): the worker initialises Sentry the same way the web does
- [ADR-011](ADR-011-image-safety-vision-judge.md): the bounded redraw loop, unchanged inside its activity

---

## References

### Code

- `src/workshop/manager.py`: in-process execution, `reap_stale`, `resume_on_boot`
- `src/api/main.py`: the boot reap and resume task
- `src/pipeline/generate.py`, `src/pipeline/cache.py`: the steps and the checkpoint primitive
- `src/pipeline/retry.py`: the billing-safe provider retry rules
- `src/workshop/records.py`: the run lifecycle and conditional saves
- `src/api/routes/workshop.py`: `_checkpointed_steps`, the progress fragment
- `render.yaml`: `CONTENT_DIR=/tmp/cantastorie-content`
- `docs/setup.md`: ephemeral disk, rolling-deploy overlap, shutdown cancellation
- Issues #158, #172, #182, #184, #199

### External

- [Temporal pricing](https://temporal.io/pricing) and [Temporal Cloud pricing docs](https://docs.temporal.io/cloud/pricing): pay-as-you-go with no base fee, $50 per million Actions, 10% support, $150 new-account credit (checked 2026-11-28)
- [Billable Actions](https://docs.temporal.io/cloud/actions): workflow starts, activity starts and retries, heartbeats that reach the server, queries received by a worker
- [Temporal Cloud regions](https://docs.temporal.io/cloud/regions): `aws-eu-central-1` Frankfurt
- [Temporal CLI server](https://docs.temporal.io/cli/server): the dev server is not for production
- [Render Workflows](https://render.com/docs/workflows), [Defining flows](https://render.com/docs/workflows-defining-flows), [Triggering runs](https://render.com/docs/workflows-running) and [Limits and pricing](https://render.com/docs/workflows-limits): steps, replay, `Retry`, `RENDER_API_KEY`, the 4 MB and 30 day limits (checked 2026-11-28)
- [Render pricing](https://render.com/pricing) and [Render free tier](https://render.com/docs/free): instance, Postgres and Key Value prices; free Postgres expires after 30 days
- [temporalio on PyPI](https://pypi.org/project/temporalio/): Python SDK 1.37.0, Python 3.11+, Pydantic data converter
- [Pydantic AI durable execution with Temporal](https://pydantic.dev/docs/ai/integrations/durable_execution/temporal/): considered and not used; it makes each model call an activity, while this design keeps a whole step as one activity

---

## Metadata

- **ADR Number**: 012
- **Created**: 2026-11-28
- **Tags**: pipeline, workshop, durability, temporal, hosting
