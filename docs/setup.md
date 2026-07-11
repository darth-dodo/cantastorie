# Deploying Cantastorie

Cantastorie has two deployed pieces and one that never deploys:

| Piece | Where | Serves |
|-------|-------|--------|
| **Web service** | Render (Docker, `render.yaml`) | The static player shell and the parent area |
| **Content bucket** | Cloudflare R2 | Published stories and prompts, fetched bucket-direct |
| **Authoring pipeline** | Your laptop only | Runs the CLI; its two API keys stay in local `.env` |

The pipeline's keys (`OPENROUTER_API_KEY`, `ELEVENLABS_API_KEY`) are **never** deployed — the running site needs no secrets. See [architecture.md → Content Storage](architecture.md#content-storage).

---

## Prerequisites

- A Cloudflare account with R2 enabled.
- The `wrangler` CLI: `npm install -g wrangler`, then `wrangler login`.
- A Render account connected to the GitHub repository.

---

## 1. The R2 bucket

The bucket **`cantastorie`** lives in the **EU jurisdiction** (data residency in the EU — the audience is Italian/Spanish families and the app handles children's context). R2 namespaces jurisdictional buckets separately, so **every `wrangler r2` command needs `-J eu`** (`--jurisdiction eu`); without it wrangler looks at the default namespace and reports "bucket does not exist". To create it (already done for the live bucket):

```
wrangler r2 bucket create cantastorie -J eu
```

The `published/` tree lives inside it as key prefixes.

### Public read

Published content is public by design. Enable the bucket's public URL:

```
wrangler r2 bucket dev-url enable cantastorie -J eu
```

This returns a `https://pub-<hash>.r2.dev` URL — the live bucket's is `https://pub-ee7647e725e84705b6c5be139919f6b8.r2.dev`. For a stable name, connect a custom domain instead (Dashboard → R2 → the bucket → Settings → Custom Domains) and add that origin to the CORS file below.

### The private pending bucket (workshop, ADR-004)

Workshop run records and staged pack artifacts live under a `pending/` prefix — and the public bucket exposes **everything** under its public URL, with no prefix scoping. Pending content therefore gets its **own private bucket** (no public URL, no custom domain, no CORS):

```
wrangler r2 bucket create cantastorie-pending -J eu
```

Set **`R2_PENDING_BUCKET=cantastorie-pending`** wherever the workshop runs (Render dashboard, local `.env`). With it unset the workshop falls back to `R2_BUCKET` — acceptable against a local/dev bucket, **never against the live public one**. The audit script (AI-390) fails on any `pending/` object found in the public bucket.

### Access logs OFF

R2 does not log object access by default — the goal is to keep it that way, so there is provably nothing recording what a child plays. Verify no event-notification pipeline is attached (a bare "no configurations found" is the healthy answer):

```
wrangler r2 bucket notification list cantastorie -J eu
```

Also confirm in the Dashboard (R2 → the bucket → Settings) that **no Logpush job** targets the bucket. This is part of "nothing about the child ever leaves the browser".

### CORS

The player fetches assets cross-origin (Render shell → R2 bucket), so the bucket must allow the player origin. The policy is version-controlled at [`deploy/r2-cors.json`](../deploy/r2-cors.json) in Cloudflare's R2 CORS schema (a `rules` array; `allowed.methods` `GET`/`HEAD`, `allowed.headers` `Range` for audio seeking, scoped to the Render origin and localhost). Apply and verify it with:

```
wrangler r2 bucket cors set cantastorie --file deploy/r2-cors.json -J eu
wrangler r2 bucket cors list cantastorie -J eu
```

Add your custom-domain and any production origins to `allowed.origins` before applying.

---

## 2. Publish a story into the bucket

The pipeline's `publish` step is the **only** path that writes to the bucket (see AI-361). Nothing reaches `published/` by hand. Once a story is approved and published, the bucket holds:

```
published/it/manifest.json          ← short TTL, the only volatile file
published/stories/{story-id}/…       ← immutable, content-hashed
published/prompts/it/…
```

---

## 3. The Render web service

1. Render → **New** → **Blueprint** → pick this repository. Render reads `render.yaml` and creates the `cantastorie` service on the **Starter** plan (always-on — the cold-start decision, see the risk log).
2. Set one environment variable on the service:
   - **`ASSET_BASE`** = the bucket's public URL **plus the `/published` prefix**, no trailing slash. For the live EU bucket that is `https://pub-ee7647e725e84705b6c5be139919f6b8.r2.dev/published` (or `https://cdn.your-domain/published` once a custom domain is attached).
3. Leave `autoDeploy` on: pushes to `main` redeploy. The Dockerfile compiles Tailwind and serves the shell; `/health` is the health check.

Without `ASSET_BASE`, the player falls back to the app's own `/static/content` mount (the dev fixtures) — useful for a smoke test, but real published stories live in R2.

---

## 4. Verify (the AI-365 acceptance)

On a phone on **cellular** (not home wifi), open the Render URL and confirm:

- [ ] A full story night plays end to end — tap the shelf (greeting), tap the cover, narration and pages run hands-free to the end screen; replay works.
- [ ] **No cookies** are set (DevTools → Application → Cookies, or remote-debug the phone).
- [ ] The page shell loads from the Render origin; **manifest and story assets load from the R2 domain** (Network tab).
- [ ] R2 **access logging/Logpush is off** (Dashboard check from step 1).

---

## Cost

- **Render Starter**: ~$7/month, always-on (the cold-start decision — a bedtime app is opened cold nightly, and the free tier's spin-down would blow the 4-second first-open budget).
- **R2**: zero egress fees; storage for the launch library is pennies.
