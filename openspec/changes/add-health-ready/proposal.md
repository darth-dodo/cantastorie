# Proposal

## Why

`/health` always answers `{"status": "ok"}`, so a deploy that boots but cannot reach R2 still passes Render's health check and takes traffic. Since H3 (AI-479) every green CI run on `main` deploys automatically, so the check Render gates on has to be able to fail (release-readiness audit item M1, GitHub #183).

## What Changes

- New `GET /health/ready`: answers 200 when every readiness check passes, 503 when any fails, with each check named and its result (`ok`, `failed` or `skipped`). It never includes credentials, endpoint URLs or exception text.
- The R2 check is a `head_bucket` on the published bucket and on the pending bucket, with a short timeout. When R2 is not configured (local and test), the check is `skipped` and readiness still passes.
- The result is cached for a few seconds so Render's polling does not turn into a stream of R2 calls, and one slow R2 response does not stall every poll.
- `render.yaml` `healthCheckPath` moves from `/health` to `/health/ready`.
- `/health` stays as the cheap liveness check, unchanged.

## Capabilities

### New Capabilities

- `service-health`: the health endpoints Render and the smoke check use: liveness (`/health`), the deployed commit (`/health/version`) and readiness (`/health/ready`).

### Modified Capabilities

(none)

## Impact

- `src/api/main.py` (or a small `src/api/routes/health.py` it includes), `render.yaml`, and new tests.
- No new dependencies: boto3 and moto are already in use.
- Operational: an R2 outage longer than the cache window makes Render mark the instance unhealthy. That is the point for a new deploy (it is not promoted), but on a running instance it can take the player page down while R2 is down. Story assets are served straight from R2, so they would be down too. `design.md` records the trade-off.
