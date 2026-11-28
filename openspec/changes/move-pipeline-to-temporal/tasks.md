# Tasks

Hosting is Temporal Cloud, pay-as-you-go, `aws-eu-central-1` (owner, 2026-11-28). Each numbered group below is one child Issue of the epic and lands in its own PR; group 6 archives the change.

Each test is written first and seen failing before the code that makes it pass. Provider calls use the existing fakes; R2 uses moto with the public and pending buckets, as in `tests/pipeline/test_publish.py`. Run vitest with `NODE_OPTIONS=--no-experimental-webstorage` on Node 26 (LESSONS.md 3).

## 1. Spike behind the flag

- [x] 1.1 Add `temporalio` with `uv add temporalio` and verify `uv lock --check` and `make check` pass
- [x] 1.2 Test first in `tests/workshop/test_engine_flag.py`: `Settings` defaults `pipeline_engine` to `inprocess`, accepts `temporal`, refuses other values, and refuses `temporal` without `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_API_KEY` and `TEMPORAL_PAYLOAD_KEY`; add the fields to `src/config.py` and `.env.example`; verify the tests pass
- [x] 1.3 Test first in `tests/workshop/temporal/test_codec.py`: the AES-GCM payload codec round-trips a payload, a payload encoded with one key fails to decode with another, and the encoded bytes do not contain a sample premise; implement `src/workshop/temporal/codec.py`; verify the tests pass
- [x] 1.4 Test first: a one-activity `SpikeWorkflow` run in `WorkflowEnvironment.start_time_skipping()` through a client built by `src/workshop/temporal/client.py` (codec and Pydantic data converter on) returns the activity's result; verify with `uv run pytest tests/workshop/temporal -m temporal`, and cache the test server download in CI (`.github/workflows/ci.yml`)
- [x] 1.5 Record the measured billable Actions for a spike run with a heartbeating activity in the ADR-012 cost section; verify the number and how it was counted are in the diff

## 2. One workflow, one activity per step

- [ ] 2.1 Test first in `tests/pipeline/test_cache.py`: with the R2 tier on, a store writes local and `pending/cache/{story-id}/{step}/{key}{suffix}` in the pending bucket; a load with an empty local folder returns the R2 bytes and writes the local copy; nothing lands in the public bucket; implement the tier in `src/pipeline/cache.py`; verify the tests pass
- [ ] 2.2 Test first: `RunRecord` gains `engine` (default `inprocess` for old records), `current_step`, `completed_steps` and `failed_step` (reuse PR #209's field if it has merged); old JSON loads unchanged; verify in `tests/workshop/test_records.py`
- [ ] 2.3 Test first in `tests/workshop/temporal/test_activities.py` with `ActivityEnvironment`: each step activity (write, safety, revise, narrate pages, narrate choice labels, illustrate, judge images, assemble, narrate prompts, stage) calls the existing step function, writes `current_step` to the record first, and returns references, never image or audio bytes; implement `src/workshop/temporal/activities.py`; verify the tests pass
- [ ] 2.4 Test first: the activities' retry policy marks `StoryRejectedError`, `ImageSafetyRejectedError`, `ContentRulesViolation`, `httpx.ReadTimeout` and provider 500 and 4xx (except 429) non-retryable; a 503 storm sends at most 9 requests; a read timeout sends exactly 1; verify the tests pass
- [ ] 2.5 Test first in `tests/workshop/temporal/test_workflow.py`: `StoryRunWorkflow` runs the steps in order, runs revise only when safety fails, stops after one revise, redraws a failing image slot at most twice, and calls `mark_staged` last; on a non-retryable failure it calls `mark_failed` with the step name; implement `src/workshop/temporal/workflow.py`; verify the tests pass
- [ ] 2.6 Test first: the same request on both engines with the same fakes stages byte-identical `story.json` under the same story id; verify the test passes
- [ ] 2.7 Test first (resume): a workflow whose worker is stopped during illustrate and restarted with an empty local folder completes, and the fake text and narration providers record no second call; verify the test passes
- [ ] 2.8 Test first (review gate): after a completed workflow the record is `staged` with `reviewed` false, the public bucket has no new key, and the worker's registered activities include no publish, unpublish or manifest-writing function; verify the tests pass
- [ ] 2.9 Test first (privacy): the recorded history of a run with a premise contains neither the premise nor the family token in clear, and the workflow id is `story-run-{run_id}`; verify the test passes
- [ ] 2.10 Replay test: a recorded history from 2.5 replays without non-determinism errors; verify in CI
- [ ] 2.11 Describe the workflow, activities and cache tier in `docs/system-overview.md`; verify every module path it names exists

## 3. Worker process in render.yaml

- [ ] 3.1 Test first in `tests/workshop/test_worker.py`: `python -m src.workshop.worker` builds a worker on `TEMPORAL_TASK_QUEUE` with the codec, the workflow and the step activities, `max_concurrent_activities=1`, and initialises Sentry and logging; implement `src/workshop/worker.py`; verify the tests pass
- [ ] 3.2 Use the `render-deploy` skill: add `cantastorie-worker` (`type: worker`, docker, Starter, Frankfurt, `autoDeploy: false`, `dockerCommand: python -m src.workshop.worker`) with its env vars (`sync: false` for secrets); excluded from previews; `PIPELINE_ENGINE` with `previewValue: inprocess` on the web; create the Temporal Cloud namespace in `aws-eu-central-1` with an API key scoped to it; verify `tests/test_render_previews.py` and a new blueprint test pass
- [ ] 3.3 Extend the CI deploy job to fire the worker's deploy hook after the web's; verify `tests/test_deploy_pipeline.py` covers both hooks
- [ ] 3.4 Add `make dev-worker` (dev server plus worker) and document it in AGENTS.md's command table; verify it starts locally and a run started with the flag on completes against the dev server
- [ ] 3.5 Document the worker, the namespace or server setup, the secrets and the `pending/cache/` lifecycle rule (`wrangler r2 bucket lifecycle add cantastorie-pending run-cache pending/cache/ --expire-days 14 -J eu`) in `docs/setup.md` and the FACTORY.md secrets table; verify the commands run as written or with `--help`

## 4. The workshop reads workflow state

- [ ] 4.1 Test first: with the flag on, workshop submit, parent submit, retry of a failed run and run again record `engine=temporal` and start `story-run-{run_id}` instead of adding a background task; a second start for the same run is treated as success and starts nothing; verify in `tests/workshop/test_routes.py` and `tests/api/test_parent_pages.py`
- [ ] 4.2 Test first (extend `tests/api/test_parent_review_gate.py` for the parent side): the workshop and parent progress fragments build their beads from `current_step` and `completed_steps` for Temporal records, and the failed screen names `failed_step`; in-process records keep the folder-based beads until group 6; verify the tests pass
- [ ] 4.3 Test first: a Temporal record still `running` whose workflow describes as failed, timed out or terminated is marked `failed` on the next progress read, at most once per `reap_min_interval_seconds`; a queued record with no worker shows the waiting state; verify the tests pass
- [ ] 4.4 Test first: boot resume and `reap_stale` skip Temporal records; verify in `tests/api/test_lifespan.py` and `tests/workshop/test_manager.py`
- [ ] 4.5 E2E: the workshop run journey passes with the flag on against the dev server (`E2E_PORT=<free port> npx playwright test`), with a screenshot of progress during narrate and of a failed run naming its step

## 5. Cutover

- [ ] 5.1 Pending the owner's yes for one paid production run: rehearsal on production with the operator's own run: start with `PIPELINE_ENGINE=temporal`, redeploy the worker during illustrate, confirm the run stages and the provider call counts match a clean run (LangSmith or OpenRouter activity); paste the evidence into the PR
- [ ] 5.2 Pending the same yes: review-gate check on production: the rehearsal story is staged and unreviewed, the shelf manifest is unchanged until approve, and approve without review is refused; paste the evidence into the PR
- [ ] 5.3 Set `PIPELINE_ENGINE=temporal` in production and record the date and the rollback command (`PIPELINE_ENGINE=inprocess`) in `docs/setup.md`; verify a parent run completes on the worker

## 6. Remove the old path

- [ ] 6.1 After a week with no live `inprocess` record, remove the in-process `execute` background path, `resume_on_boot`, `reap_stale`, the lifespan task and `_checkpointed_steps`, and make `temporal` the only engine; verify `make check`, `uv run pytest` (coverage at or above the floor) and `npx vitest run` are green
- [ ] 6.2 Remove the setup.md notes on ephemeral checkpoints, rolling-deploy double generation and shutdown cancellation; update `docs/architecture.md` (pipeline key decision, "Why no framework", "How runs execute", project structure), `docs/system-overview.md`, and mark ADR-001 and ADR-005 superseded in part by ADR-012 (status lines and the ADR index); verify every link resolves
- [ ] 6.3 Set ADR-012 to Accepted with the hosting outcome and the measured monthly cost; verify the index row matches

## Workflow follow-up

- Archive the change with `openspec-archive-change` in the build PR that completes group 6.
- Close #172 if PR #209 has not; comment on #184 (now only the cap check race), #158 (resume half covered) and #199 (not covered).
