---
name: render-deploy
description: Use when changing render.yaml, the Dockerfile, the CI deploy job, runtime config or env vars for Cantastorie, checking whether a deploy went live, rolling back, or debugging a failed Render deploy or health check
---

# Render deploy

Cantastorie runs as one Docker web service on Render (Frankfurt, Starter plan, always on) at https://cantastorie.onrender.com. Published stories live in Cloudflare R2, not in the deploy. Full setup and rollback detail is in `docs/setup.md`.

## Files

| File | Role |
|---|---|
| `render.yaml` | Blueprint: service, plan, region, health check, env vars, preview environments. `autoDeploy: false` |
| `Dockerfile` | Stage 1 compiles Tailwind, then runs `uvicorn src.api.main:app` on `$PORT` (default 8000); has a `/health` HEALTHCHECK |
| `.github/workflows/ci.yml` | Jobs behind the required `CI Success` check, then the `deploy` job (main pushes only): `curl -fsS -X POST "${RENDER_DEPLOY_HOOK_URL}&ref=${GITHUB_SHA}"`. Skips with a notice when `RENDER_DEPLOY_HOOK_URL` is unset |
| `src/api/main.py` | `/health` (Render's health check). `/health/version` (the deployed commit, from `RENDER_GIT_COMMIT`) arrives in a later PR |
| `docs/setup.md` | Env var table, R2 buckets, Clerk, deploy hook, rollback, preview environments |

## Rules

- Region `frankfurt` and the service name are permanent. Never change them in `render.yaml`.
- Plan changes cost money: ask Abhishek first.
- `autoDeploy: false` stays. Main deploys only through the CI `deploy` job after `CI Success`. Never switch Render to auto-deploy on commit.
- Secrets are `sync: false`. Abhishek sets them in the Render dashboard. Never put a value in `render.yaml`, a commit or chat, and never read `.env`.
- The `render` CLI v2 has no env-var command. Env vars are set in the dashboard or through the Render REST API, never with the CLI.
- New env var: add it to `render.yaml` (with `sync: false` if secret, and a `previewValue` that keeps previews read-only), `src/config.py`, `.env.example`, the secrets table in `FACTORY.md` and the variable table in `docs/setup.md`.
- Keep `/health` cheap and free of R2, OpenRouter and Clerk calls, so a vendor outage never restarts the app.
- Previews are read-only by construction (blank Clerk and R2 values). Never give a preview a live key.
- After changing a dashboard env var, redeploy with the hook (`curl -fsS -X POST "${RENDER_DEPLOY_HOOK_URL}"`, which Abhishek runs; the URL is a secret) or Manual Deploy.

## Before opening a deploy PR

For any change to `render.yaml`, `Dockerfile` or the CI `deploy` job:

1. `make check` and `uv run pytest` green (`tests/test_render_previews.py` covers the preview values).
2. `docker build -t cantastorie:local .` succeeds.
3. Smoke test the image with no keys set: `docker run --rm -p 8001:8001 -e PORT=8001 cantastorie:local`, then `curl -fsS localhost:8001/health` answers 200. R2 and Clerk are unset, so the parent area and workshop answer 404; `/health` must still answer.
4. Do not trigger the deploy hook from a branch. It deploys on main only.

## Verify a deploy

1. The merged PR's `CI Success` check is green: `gh run list --branch main --limit 1`.
2. The `Deploy to Render` job in that run is green, or shows the "Deploy skipped" notice (hook secret unset, nothing deployed).
3. If it is not live, the Render dashboard (Events) shows the deploy state for that commit.
4. `curl -fsS https://cantastorie.onrender.com/health` returns 200. Once `/health/version` exists, it prints the merged commit SHA: a different SHA means deploy lag, wait and recheck.
5. The home page loads over HTTPS and the player shelf appears (no console errors).

## Rollback

From `docs/setup.md`, fastest first:

- Render dashboard: service, Events, the last good deploy, Rollback. `main` still holds the bad commit, so also revert it.
- Durable: `git revert <bad-sha>`, PR, merge; CI runs and the `deploy` job ships it.
- Manual redeploy: Render Manual Deploy, "Deploy a specific commit", or `curl -fsS -X POST "${RENDER_DEPLOY_HOOK_URL}&ref=<good-sha>"`.

Story content rolls back through the publish pipeline, not Render.

## When it breaks

| Symptom | Likely cause |
|---|---|
| Deploy never starts after a green main | `RENDER_DEPLOY_HOOK_URL` unset in GitHub Actions (the job skips with a notice), or the `deploy` job did not run because `CI Success` failed |
| Health check fails, deploy never goes live | App not listening on `$PORT`, or a boot crash: read the deploy log. With `R2_ENDPOINT_URL` set, the app refuses to boot unless `R2_PENDING_BUCKET` names a separate private bucket |
| App refuses to boot after setting `CLERK_JWKS_URL` | `CLERK_ISSUER` is required whenever the JWKS URL is set |
| `/parent` or `/workshop` answer 404 in production | Clerk keys unset or blank in the dashboard |
| Parent review shows "no staged story" | The R2 token lacks access to the pending bucket (`docs/setup.md`, token scope) |
| Player shows the clouds screen or no stories | `ASSET_BASE` wrong, or the R2 CORS policy (`deploy/r2-cors.json`) lacks the origin |
| Docker build fails at the Tailwind stage | `npm ci` or `make build-css` broken: reproduce with `docker build .` |
| Preview deploy shows live data or can publish | A preview override is missing in `render.yaml`: fix before merging |
