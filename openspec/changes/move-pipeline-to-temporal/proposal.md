# Proposal

## Why

Story runs execute inside the web process and rely on a checkpoint folder that Render wipes on every deploy (`CONTENT_DIR=/tmp/cantastorie-content`), so a deploy in the middle of a run re-buys every finished step, and a rolling deploy can run the same story twice (`docs/setup.md` records both as accepted costs). The owner's goal of 2026-11-28 is to move the pipeline to Temporal; [ADR-012](../../../docs/adr/ADR-012-durable-story-runs-on-temporal.md) (Proposed) records the trade-off against keeping the in-process runs or using a simple Redis queue.

## Hosting: Temporal Cloud (decided)

The owner decided on 2026-11-28: "Cloud". Runs use Temporal Cloud, pay-as-you-go, in `aws-eu-central-1` Frankfurt. Self-hosting on Render was considered and not chosen. Prices were checked on 2026-11-28, and every option also needs the Render background worker ($7 a month).

| Option | Monthly cost | Notes |
|---|---|---|
| Temporal Cloud, pay-as-you-go, `aws-eu-central-1` Frankfurt (chosen) | About $7.25 total at 100 runs, about $10 at the global cap | No base fee; $50 per million Actions plus 10% support; about 40 Actions per run (unverified); $150 credit for 90 days |
| Self-hosted Temporal on Render (considered, not chosen) | About $40 total (server private service Standard $25, Postgres Basic-256mb $6, storage about $1.50, worker $7) | No new vendor; we own upgrades, schema migrations and backups |
| Temporal dev server | $0 | Not for production (Temporal's own docs); used for local development and CI in every option |

## Alternatives rejected

ADR-012 records each one with its reasons:
- Keeping the in-process runs and patching them.
- A Redis queue (RQ or arq).
- Render Workflows, rejected by the owner on 2026-11-28 ("Temporal"). It would have meant no new vendor at about $2 to $3 a month. But it needs an account-wide `RENDER_API_KEY` on the web service, its retries cannot exclude safety rejections or read timeouts, a Frankfurt workflow service is unconfirmed, and it has only been GA since 2026-09-01.

## What Changes

- A feature flag `PIPELINE_ENGINE` (`inprocess` by default, or `temporal`) chooses how a new run executes. The in-process path stays until the cutover, then is removed.
- With the flag on, a run is a Temporal workflow with one activity per pipeline step: write, safety, revise, narrate, illustrate, image safety, assemble, narrate prompts and stage. The activities call the existing step functions; cache keys, safety gates and their bounds do not change.
- A new Render background worker (`cantastorie-worker`, same image) runs the activities. It is excluded from previews. The web service only starts workflows and reads run records.
- The artifact cache gains a second tier in the private pending bucket, so a step retried on a new container finds what it already bought.
- Run records gain `engine`, the current step, the completed steps and the failed step. The workshop and parent progress views read these instead of the container's checkpoint folders.
- Workflow payloads are encrypted before they leave the app; workflow ids carry only the random run id.
- New settings: `PIPELINE_ENGINE`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_API_KEY`, `TEMPORAL_PAYLOAD_KEY`.
- After the cutover: **BREAKING (internal)** the in-process background task, boot resume and stale-run reaper are removed.

## Safety and review gate

This touches generation. The workflow ends when the story is staged in the private bucket. No workflow or activity publishes: the worker registers no publish function, and approve and reject stay HTTP routes, with the family lane's reviewed-digest check unchanged. A text safety, image safety or content-rules rejection is never retried, so the engine cannot re-roll a judge until it passes, and the revise and redraw bounds stay as they are. The child never meets an unreviewed story.

## Capabilities

### New Capabilities
- `story-run-execution`: how a requested story run executes, survives restarts and deploys, retries, reports progress and failure, and stops at the review gate.

### Modified Capabilities
None. (`service-health` is unchanged: the web service's `/health/ready` does not depend on the worker or on Temporal.)

## Impact

- Code: new `src/workshop/temporal/` (client, codec, workflow, activities) and `src/workshop/worker.py`; `src/workshop/manager.py` and `src/workshop/records.py` (engine, progress fields); `src/pipeline/cache.py` (R2 tier); `src/api/routes/workshop.py` and `src/api/routes/parent.py` (start workflows, read progress from the record); `src/api/main.py` (boot resume only for in-process records, then removed); `src/config.py`.
- Dependency: `temporalio` (added by the spike task, not in this change). Payload encryption uses the existing `cryptography` dependency.
- Infrastructure: a Render background worker; a Temporal namespace (Cloud) or a Temporal server and Postgres (self-hosted); an R2 lifecycle rule expiring `pending/cache/`.
- Docs: ADR-012, `docs/architecture.md`, `docs/system-overview.md`, `docs/setup.md`, the FACTORY.md secrets table; ADR-001 and ADR-005 marked superseded in part at cutover.
- Issues: closes #172 (failed step) if PR #209 has not; reduces #184 to the cap check race; covers the resume half of #158; does not cover #199 (staged stories and records still have one copy) or #182 (redraw prompts).
