# Design

## Context

`/health` and `/health/version` are defined inline in `create_app()` (`src/api/main.py`). R2 is reached through boto3 clients built per module (`src/pipeline/publish.py`, `src/workshop/records.py`). `Settings` already refuses to load a partial R2 config, and the app refuses to build at all if settings are invalid, so a process that answers HTTP has loaded settings. Tests use moto for S3. See proposal.md for why.

## Goals / Non-Goals

**Goals:** a readiness answer Render can gate deploys on, that cannot leak secrets and cannot hang.

**Non-Goals:** checking OpenRouter, Clerk or Sentry (each would make readiness depend on a third party for no deploy-time benefit); a metrics endpoint.

## Decisions

- **Its own module, `src/api/routes/health.py`.** All three endpoints move there with a router, so the readiness logic and its cache sit next to their tests instead of growing `create_app()`. Alternative: keep everything inline, which is simpler but mixes I/O and caching into app wiring.
- **`head_bucket` on both buckets.** It is the cheapest call that proves the endpoint, the credentials and the bucket all work, with no data read. Alternative: list one key, which costs more and depends on content.
- **Short boto3 timeouts (connect and read about 2 s, one attempt)**, run in a thread so the event loop is never blocked. An R2 hang becomes `failed` within a bounded time.
- **A 10-second in-process cache of the whole result.** Render polls the health check frequently. Caching keeps R2 calls to at most one per 10 s per instance, and keeps a slow R2 from stalling consecutive polls. Alternative: no cache, which is simpler but turns every poll into two R2 calls.
- **Fixed names and values in the body.** The body is built only from the check names and the three literal values, so nothing from an exception or the config can reach it. Failures are logged server-side with the exception class only.
- **Unconfigured R2 is `skipped`, not `failed`.** Local development and tests have no R2, and readiness must not fail there.

## Risks / Trade-offs

- [An R2 outage on a running instance] → Render marks it unhealthy and the player page can go down while R2 is down. Story audio and images are served straight from R2, so stories would not play anyway; the 10 s cache plus Render's own retry threshold absorb single blips. If this proves too strict, readiness can drop to `r2_pending` only (the workshop and parent area need it, the child player does not).
- [A cold start where R2 is slow] → the first poll may fail until R2 answers; Render retries during a deploy before declaring it failed.

## Migration Plan

Ship the endpoint and the `render.yaml` change in one PR. Rollback: set `healthCheckPath` back to `/health` (one line) and redeploy; `/health` is unchanged.
