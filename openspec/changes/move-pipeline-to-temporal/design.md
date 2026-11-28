# Design

## Context

See proposal.md (Why) and ADR-012 for the evidence. The constraints that shape the approach:

- `generate_story` (`src/pipeline/generate.py`) is one synchronous function over step functions that each go through `run_step` (`src/pipeline/cache.py`): hash the inputs, load or produce, store atomically.
- `author_story` (`src/pipeline/steps/revise.py`) holds the write, safety, revise, safety loop; `illustrate_safely` (`src/pipeline/steps/image_safety.py`) holds the illustrate, judge, redraw loop (at most 2 redraws). Both raise typed rejections (`StoryRejectedError`, `ImageSafetyRejectedError`, `ContentRulesViolation`).
- `src/pipeline/retry.py` already retries at the transport, at most three sends, only when the provider never served the request.
- `RunRecord` saves are conditional on the ETag (`ConcurrentModificationError` on a stale write). Review fields (`reviewed`, `reviewed_digest`) are written by the web after staging.
- The progress fragment (`src/templates/workshop/_progress.html`) shows the beads write, revise, safety, narrate, illustrate, assemble from `_checkpointed_steps`, which lists folders on the web container's disk.
- PR #209 (open) adds a `failed_step` for #172 on the in-process path. Whichever lands second reuses the other's field.

## Goals / Non-Goals

**Goals:**
- A Temporal run produces the same staged story as an in-process run for the same inputs.
- Each step is retried, timed out and recorded on its own.
- The web service can be deployed or scaled without touching a live run.
- One environment variable rolls back until the old path is removed.

**Non-Goals:**
- Changing any prompt, model, cache key, safety rule or bound (#182 stays separate).
- Moving publish, approve or reject into the engine, or waiting for approval inside a workflow.
- Backing up run records or staged stories (#199).
- Batch generation (#158); this change makes it a loop of starts, but does not build it.
- Per-family cap atomicity across web instances (the rest of #184).

## Decisions

**1. One activity per pipeline step, not per model call.**
Activities: `write`, `safety`, `revise`, `narrate_pages`, `narrate_choice_labels`, `illustrate` (character sheet and one image slot set), `judge_images`, `assemble`, `narrate_prompts` (Italian only) and `stage`, plus `mark_running`, `mark_step` folded into each activity's first line, `mark_staged` and `mark_failed`. The workflow holds the two loops (revise once, redraw at most twice per failing slot) by calling the same functions `author_story` and `illustrate_safely` use, split at their existing seams. Alternative: Pydantic AI's Temporal integration, which makes every model call an activity. Rejected: more Actions, ties the agents to the engine, and the cache already gives per-artifact idempotency inside a step.

**2. Activities pass references, not bytes.**
Inputs and results are small Pydantic models: run id, story id, cache keys, the `Story` text model, verdict summaries. Images and audio never enter workflow history (Temporal's payload limit is 2 MB, and content should stay in our bucket).

**3. A second cache tier in the private pending bucket.**
`ArtifactCache` gains an optional R2 tier at `pending/cache/{story-id}/{step}/{key}{suffix}`. A load checks local disk, then R2 (writing the local copy on a hit); a store writes local, then R2. Without it a retried activity on a new container would re-buy everything, which is today's problem. An R2 lifecycle rule expires `pending/cache/` after 14 days. Alternative: a Render persistent disk on the worker. Rejected: disks pin a service to one instance and disable zero-downtime deploys, and the bucket already holds everything else durable.

**4. Workflow id is `story-run-{run_id}` with reuse policy "allow duplicate failed only".**
A second start for a running or completed run is refused by the server, which is the run-level lock `docs/setup.md` says is missing. A failed run can be re-queued and started again under the same id, and finds its cached artifacts. The web treats "already started" as success.

**5. Retry policy per activity.**
Default: initial interval 5 s, backoff 2, at most 3 attempts. Non-retryable error types: `StoryRejectedError`, `ImageSafetyRejectedError`, `ContentRulesViolation`, `httpx.ReadTimeout`, provider `HTTPStatusError` with status 500 or any 4xx except 429. The transport's own three sends stay; the activity retry covers what the transport refuses on purpose only when the request never reached the provider (connect errors, 429 or 502/503/504 after three sends, R2 errors). Timeouts: `start_to_close` 15 minutes for illustrate and narrate, 5 minutes for the rest; `heartbeat_timeout` 60 s for illustrate and narrate, which heartbeat after each image or clip. Workflow execution timeout: 2 hours.

**6. Progress lives in the run record, written by activities.**
Each step activity first saves `current_step` (and appends the previous step to `completed_steps`) with a conditional write. The views read the record; they never query the worker. Alternative: a Temporal query handler. Rejected: each query a worker answers is a billable Action and the progress view polls every 2 seconds, so queries would cost more than the runs, and they fail while the worker is down.

**7. Reconcile closed workflows on read, throttled.**
For a `temporal` record that says `queued` or `running`, the progress route calls describe (not a billable Action) at most once per `reap_min_interval_seconds`. A closed workflow (failed, timed out, terminated, cancelled) whose record was not updated is marked `failed` with the reason and the last `current_step` as the failed step. This replaces the timer-based reaper for Temporal records.

**8. Payload codec with AES-GCM, key from `TEMPORAL_PAYLOAD_KEY`.**
A `PayloadCodec` on the client and worker encrypts every payload with the existing `cryptography` dependency. Workflow input is the run id plus the store token needed to load the record, both inside the encrypted payload; the premise is read by the `write` activity from the record, not passed. Temporal Cloud (or a self-hosted server) sees ciphertext, the run id, activity names and timings.

**9. A Render background worker on the same image.**
`cantastorie-worker`, `type: worker`, `runtime: docker`, `dockerCommand: python -m src.workshop.worker`, Starter, Frankfurt, `autoDeploy: false`, deployed by the same CI hook flow. It holds `OPENROUTER_API_KEY`, the R2 keys, Sentry, LangSmith and the Temporal settings; it holds no Clerk keys. `max_concurrent_activities=1` keeps one generation step running at a time, which keeps memory and provider concurrency as they are today. The worker is excluded from previews.

**10. Local development and CI use the dev server.**
`make dev-worker` runs `temporal server start-dev` and the worker. Workflow tests use `temporalio.testing.WorkflowEnvironment.start_time_skipping()`; the binary download is cached in CI. Activity tests use `ActivityEnvironment` with the existing provider fakes and moto, so most tests need no Temporal server.

**11. The flag is read at submit and stored on the record.**
`PIPELINE_ENGINE` picks the engine for a new run; `execute`, retry and run again follow the record's `engine`. Boot resume and `reap_stale` skip `temporal` records. This keeps live runs on the engine they started on when the flag flips.

## Risks / Trade-offs

- [Two retry layers multiply sends] → The activity retry is limited to error types the transport never retried because the provider was not reached; a test counts sends for a 503 storm (at most 3 x 3) and for a read timeout (exactly 1).
- [Workflow non-determinism after a code change while runs are open] → The workflow only sequences activities; changes to its order use `workflow.patched`; replay tests run recorded histories in CI.
- [Worker down, runs pile up] → Records stay `queued` with a waiting note; Render restarts the worker; Sentry alerts on worker crashes; the global cap bounds the pile.
- [R2 cache tier doubles writes for large images] → Writes are once per artifact; intermediate storage is expired after 14 days; the private bucket is already written per step by staging.
- [Concurrent record writes from worker and web] → The web writes review fields only after `staged`, the worker never writes after `staged`; both use conditional writes and retry on `ConcurrentModificationError`.
- [LangSmith shows one trace per activity] → Every activity runs under `run_context(run_id)` so traces group by run id.
- [Vendor dependency (Cloud)] → The flag returns to in-process until removal; self-hosting uses the same code.

## Migration Plan

1. Ship the spike with the flag off in production. Nothing changes for users.
2. Ship the workflow, activities and cache tier; tests only.
3. Ship the worker service in `render.yaml`; create the Temporal Cloud namespace in `aws-eu-central-1` and set its secrets; the worker idles.
4. Ship the workshop changes; set `PIPELINE_ENGINE=temporal` on the operator's own runs in a rehearsal: start a run, redeploy the worker during illustrate, confirm completion and compare provider call counts with a clean run (LangSmith or OpenRouter activity).
5. Cutover: set `PIPELINE_ENGINE=temporal` in production. Watch a week of runs and one month of Temporal usage against the ADR estimate.
6. Remove the in-process path after no `inprocess` record has been live for a week.

Rollback: until step 6, set `PIPELINE_ENGINE=inprocess`; Temporal runs already open finish on the worker. After step 6, revert the removal PR.

## Open Questions

- The exact Actions per run, measured in the spike, which only confirms or adjusts the ADR's cost estimate.
- Whether the Render Blueprint key to exclude one service from previews is per service or needs the previews block restructured (the `render-deploy` skill checks this in task 3).
