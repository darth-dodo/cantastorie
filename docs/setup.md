# Deploying Cantastorie

Cantastorie has two deployed pieces and one that never deploys:

| Piece | Where | Serves |
|-------|-------|--------|
| **Web service** | Render (Docker, `render.yaml`) | The static player shell and the parent area |
| **Content bucket** | Cloudflare R2 | Published stories and prompts, fetched bucket-direct |
| **Authoring pipeline** | Your laptop only | Runs the CLI; its two API keys stay in local `.env` |

The pipeline's keys are **never** deployed — the running site needs no secrets. `OPENROUTER_API_KEY` is the one key that runs the default pipeline (ElevenLabs is retired — [ADR-004](adr/ADR-004-narration-deepgram-voxtral.md)). Two pipeline-only keys are the bounded exceptions: `DEEPGRAM_API_KEY` for the slice 6 word-timing pass (OpenRouter does not carry the Deepgram models), and `MISTRAL_API_KEY` for voice cloning only, once Nonna Narrates ships ([ADR-008](adr/ADR-008-narration-gemini-defaults-mistral-cloning.md)). See [architecture.md → Content Storage](architecture.md#content-storage).

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

### The private pending bucket (workshop, ADR-005)

Workshop run records and staged story artifacts live under a `pending/` prefix — and the public bucket exposes **everything** under its public URL, with no prefix scoping. Pending content therefore gets its **own private bucket** (no public URL, no custom domain, no CORS):

```
wrangler r2 bucket create cantastorie-pending -J eu
```

Like the public bucket, it is EU-jurisdiction, so every `wrangler r2` command against it needs `-J eu` too.

Set **`R2_PENDING_BUCKET=cantastorie-pending`** wherever R2 is configured: the Render dashboard, the `R2_PENDING_BUCKET` GitHub Actions secret (the R2 Bucket Audit job loads the same settings), and a local `.env` that sets `R2_ENDPOINT_URL`. There is **no fallback** to `R2_BUCKET`. With `R2_ENDPOINT_URL` set, the app refuses to start if `R2_PENDING_BUCKET` is unset or equal to `R2_BUCKET`. Without an endpoint (local dev, moto tests) the check is skipped, and a single-bucket local setup must name that bucket in both variables explicitly.

The audit (`python -m src.pipeline.cli audit`, run by CI on every push to `main`) sweeps the public bucket's `pending/` prefix and fails on any object it finds there.

**Token scope.** Render and CI use one R2 API token for both buckets. Publishing copies objects from `cantastorie-pending` into `cantastorie`, and all pending reads and writes go to `cantastorie-pending`. So that token needs **Object Read & Write on both `cantastorie` and `cantastorie-pending`**. A token scoped only to the public bucket does not fail loudly: the parent review page shows "no staged story" (a 404), because the staged-story read in `src/api/routes/parent.py` swallows the exception and returns nothing.

#### Migrating to the private pending bucket

Do these in order, before merging the change that enforces the separate bucket (AI-469):

1. Create the bucket: `wrangler r2 bucket create cantastorie-pending -J eu`.
2. Scope the R2 API token used by Render and CI to Object Read & Write on both `cantastorie` and `cantastorie-pending`.
3. Inventory `pending/` in the **public** bucket. To list it, run `uv run python -m src.pipeline.cli audit` locally from the enforcing branch, with the live R2 vars and `R2_PENDING_BUCKET=cantastorie-pending` in `.env`. Every `pending/` key is reported. Copy in-flight run records and staged stories to `cantastorie-pending`, then delete them from the public bucket. Use `wrangler r2 object get` / `put` / `delete … -J eu` for single keys. Treat every family token found under `pending/{token}/` as leaked, and rotate or re-provision it.
4. Set `R2_PENDING_BUCKET=cantastorie-pending` in the Render dashboard.
5. Add the GitHub Actions secret `R2_PENDING_BUCKET=cantastorie-pending`.
6. Merge.
7. Confirm the deploy is live and the `main` R2 Bucket Audit job is green.

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

Add your custom-domain and any production origins to `allowed.origins` before applying. CORS rules do not follow domain changes automatically: after attaching a custom domain later, add it to `deploy/r2-cors.json` and re-run the `cors set` command above.

---

## 2. Publish a story into the bucket

The pipeline's `publish` step is the **only** path that writes to the bucket (see AI-361). Nothing reaches `published/` by hand. Once a story is approved and published, the bucket holds:

```
published/it/manifest.json          ← short TTL, the only volatile file
published/stories/{story-id}/…       ← immutable, content-hashed
published/prompts/{lang}/…
```

### Spoken prompts for every language (H6, AI-481)

A story publish only carries Italian prompts (a generation run stages them for `it` alone), so the other languages' greeting, story-start, end, audio-retry and offline lines are published by a separate operator command. The lines live in `UTTERANCE_TEXTS` in `src/pipeline/steps/utterance_texts.py`; Italian, Spanish and English are the final copy from `docs/product.md`, and the Greek, German, Bulgarian, Russian and Marathi sets are machine-drafted and **pending native review**, so get those lines reviewed before running against production.

```bash
# 1. See the plan: no TTS call, no write. Lists every line, cached or not.
uv run cantastorie publish-prompts --language all --dry-run

# 2. Publish one language (or all) to the shared public shelf.
uv run cantastorie publish-prompts --language es --yes
uv run cantastorie publish-prompts --language all --yes
```

- **What it writes**: five WAVs under `published/prompts/{lang}/{name}.{hash}.wav` (immutable, hashed names), then the language's `published/{lang}/manifest.json` `prompts` map. The manifest write goes through the same IfMatch-and-retry helper as publish and unpublish, so a concurrent story publish is never lost. Stories in the manifest are left alone.
- **Guard**: without `--yes` (and without `--dry-run` or `--local`) the command refuses, because it writes the shared public bucket. It also refuses a language with no live `published/{lang}/manifest.json` (creating one would publish an empty shelf), as `--local` does, and exits 1; `--force` creates it. A dry run reports such a language as `would refuse (no manifest; needs --force)` and carries on with the rest.
- **Idempotent**: audio is cached under `content/_prompts/` (the narrate step's content-addressed cache), uploads skip objects that already hold the same bytes, and an unchanged manifest is not rewritten. A rerun costs zero TTS calls and writes nothing. Run it from the same checkout each time, or the cache is cold.
- **Complete languages are skipped**: a language whose live manifest already lists all five prompts is reported `skipped (complete)` (in a dry run too) and costs nothing. Italian is complete from story publishes, so `--language all` leaves its reviewed audio alone. Pass `--force` to re-narrate a complete language anyway; for `it` that lasts only until the next Italian story publish, which copies `pending/staged/prompts/it/` over it again.
- **Cost**: a cold run is 7 languages × 5 lines = **35 TTS calls** (Gemini 3.1 Flash TTS via OpenRouter, ADR-008); Italian is skipped as complete. Each line is a few seconds of audio, so a full run should cost well under US$1. That figure is **unverified**: check the OpenRouter usage page after the first run.
- **Offline prompt**: the player fetches the offline line **same-origin**, from `/static/content/{lang}/prompts/offline.wav` (the clouds screen shows exactly when R2 is unreachable), so publishing to R2 does not cover it. Run the `--local` form below and commit the files so the deployed app serves them.

#### Dev fixtures

`--local` writes the same audio into `src/static/content/{lang}/prompts/` under the fixed dev names (`greeting.wav`, `story-start.wav`, `end.wav`, `audio-retry.wav`, `offline.wav`) and points an existing `src/static/content/{lang}/manifest.json` at them. It never creates a missing manifest, which would turn the dev offline screen into an empty shelf. It shares the cache with the R2 run, so after a production run it costs no TTS. It needs no `--yes`, and it has no complete-language skip: `--local` always writes `it` too, replacing the Italian dev chimes with spoken lines (five TTS calls on a cold cache).

```bash
uv run cantastorie publish-prompts --language all --local --dry-run
uv run cantastorie publish-prompts --language all --local
```

Writing `it` replaces the committed chime stand-ins with spoken lines. `scripts/generate_dev_story.py` still writes those chimes (`story-start.wav`, `end.wav`, `audio-retry.wav`, `offline.wav` under `src/static/content/it/prompts/`), so rerunning it undoes the Italian `--local` run: run `publish-prompts --language it --local` again afterwards (free from the cache), or restore the four files from version control. Writing `en` gives the English dev shelf prompts; the E2E specs pin the Italian shelf, so they hold either way, but rerun the suite after committing. Until prompts exist for a language, the player stays silent where a prompt would play: no greeting, no start or end line, and a silent sleeping bird. It never blocks or errors (`tests/js/playback.test.js`).

---

## 3. The Render web service

1. Render → **New** → **Blueprint** → pick this repository. Render reads `render.yaml` and creates the `cantastorie` service on the **Starter** plan (always-on — the cold-start decision, see the risk log).
2. Set the environment variables on the service:
   - **`ASSET_BASE`** = the bucket's public URL **plus the `/published` prefix**, no trailing slash. For the live EU bucket that is `https://pub-ee7647e725e84705b6c5be139919f6b8.r2.dev/published` (or `https://cdn.your-domain/published` once a custom domain is attached).
   - The R2 publish target, all declared `sync: false` in `render.yaml`: `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE`, and **`R2_PENDING_BUCKET`** (the private bucket above). The app will not boot with an endpoint set and the pending bucket missing or equal to `R2_BUCKET`.
3. Wire up CI-gated deploys (below). `render.yaml` sets `autoDeploy: false`, so a push to `main` no longer redeploys by itself. The Dockerfile compiles Tailwind, installs the exact dependency set from `uv.lock`, and serves the shell; `/health` is the health check.
4. **Ephemeral disk**: `render.yaml` points `CONTENT_DIR` and `STAGING_DIR` at `/tmp` because Render's filesystem is wiped on every deploy. Workshop run records and staged artifacts survive anyway — they persist to the R2 pending bucket (ADR-005) — but anything only on the container disk is gone at the next deploy. Inspect staged stories through the workshop UI, not the filesystem.

Without `ASSET_BASE`, the player falls back to the app's own `/static/content` mount (the dev fixtures) — useful for a smoke test, but real published stories live in R2.

### `CONTENT_DIR` and the resume cost (B5, AI-477)

`CONTENT_DIR` is the pipeline's `ArtifactCache` (ADR-001): every step's output — outline, draft, safety verdict, gloss, narration audio, illustrations — is written there the instant it's produced, keyed by a hash of its inputs, so a resumed run finds completed steps as pure lookups instead of re-buying them. The app now resumes any run still `queued`/`running` on boot (a FastAPI `lifespan`, `src/api/main.py`), which is exactly when this cache matters most: the deploy that just restarted the process is also the deploy that wiped `/tmp`.

**Decision: accept the cost, no Render persistent disk for now.** A resumed run after a deploy re-pays the API calls for every step that hadn't finished before the restart — its cache is gone, so those steps run from scratch even though the R2 run record and any *already-staged* artifacts are untouched. This is bounded and rare in practice:

- It only affects a run genuinely interrupted mid-generation, not staged/approved/rejected work (that's durable in R2 either way).
- H3 gates `autoDeploy` on CI passing, so most deploys stop landing mid-generation by accident — the remaining trigger is a real crash or a deliberate redeploy while a run happens to be live.
- The product's volume is household-scale (ADR-005) — one story per run (AI-480), runs measured in minutes, rarely concurrent — so the worst case is re-buying a handful of provider calls for one run, not a fleet of them.
- A persistent disk is cheap in isolation, but it's still standing infrastructure (provisioning, attaching to the Starter instance, and — because Render disks pin a service to one instance — a constraint the app already accepts implicitly, not one this decision should be the reason to make explicit) for a cost that's already small and self-bounding.

Revisit this if run volume grows enough that repeated deploys start re-buying real money, or once H3's CI-gated deploys still land mid-run often enough to be a pattern worth measuring — at that point a small disk mounted at `CONTENT_DIR` is the straightforward fix, not an architecture change.

### Rolling-deploy overlap and shutdown cancellation (B5, AI-477)

Render's deploys are zero-downtime: the new instance starts, passes `/health`, and only then is the old one terminated — so for a window during every deploy, **two instances of the app are alive at once**. Both run the boot `lifespan`. If a run is `running` when a deploy starts, both the outgoing and the incoming instance can call `resume_on_boot()` and pick up the *same* record — there is no distributed lock across instances, only the `RunStore`'s own optimistic-concurrency `save()` (`IfMatch` / `ConcurrentModificationError`).

**Accepted for now, not fixed:**

- **No corruption.** Whichever instance saves second with a stale etag gets `ConcurrentModificationError`, not a silent overwrite — the record itself stays consistent.
- **Double generation cost.** The etag guard only protects the *write*. Both instances' `execute()` calls run the pipeline's step functions before either one saves, so the same narration/image/LLM calls can be paid for twice for one run during the overlap window. This is a real-money cost, not just wasted CPU, and there is no fix in this change — just this note. The product's volume (household-scale, ADR-005) and the overlap window's short duration keep the blast radius small; revisit with a run-level lock (e.g. a conditional "claim" write) if double-billing ever actually shows up in provider usage.
- **Shutdown cancellation detaches rather than stops.** `lifespan`'s shutdown calls `resume_task.cancel()`, which raises `CancelledError` into the task at its next `await` — but when that `await` is `asyncio.to_thread(...)`, the underlying OS thread keeps running to completion in the background; Python cannot forcibly kill a thread. A cancelled boot resume's generation call can keep making provider calls and writing artifacts after the owning instance has otherwise shut down, orphaned and unobserved. This is a known limitation of `asyncio.to_thread` cancellation generally, not specific to this feature, and is accepted rather than worked around here.

### Deploys are gated on CI (AI-479)

Production ships only after CI passes. The `deploy` job in `.github/workflows/ci.yml` runs on a push to `main`, waits for the `CI Success` gate (lint, types, Python and JS tests, E2E, the security scan, the Docker build and the R2 audit), and then POSTs to a Render **deploy hook**. It never runs on pull requests. Without the hook secret it skips with a notice instead of failing, so forks stay green. `tests/test_deploy_pipeline.py` holds these rules in place.

One-time operator setup:

1. Render → the `cantastorie` service → **Settings** → **Deploy Hook** → copy the URL. Treat it as a credential: anyone holding it can trigger a deploy.
2. GitHub → the repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, named **`RENDER_DEPLOY_HOOK_URL`**, with the hook URL as its value.
3. Render → the service → **Settings** → **Build & Deploy** → confirm **Auto-Deploy** is **Off**. The dashboard setting can override `render.yaml`. If it is left on, Render still deploys every push before CI finishes, and the gate does nothing.
4. Merge something small to `main` and check that the `Deploy to Render` job runs last and that a new deploy appears in Render's **Events** tab.

Each deploy ships exactly the commit CI tested: the job appends `&ref=$GITHUB_SHA` to the hook URL. A newer commit on `main` that is still in CI never rides along on an older run's deploy.

### Rolling back

Pick whichever is fastest:

- **Render dashboard (fastest).** Service → **Events** (or **Deploys**) → find the last good deploy → **Rollback**. This redeploys that build's image without rebuilding. Remember that `main` still holds the bad commit: the next green merge redeploys it unless you also revert it.
- **Revert on `main` (durable).** `git revert <bad-sha>` → PR → merge. CI runs on the revert and the `deploy` job ships it. Use this to make the rollback permanent after a dashboard rollback.
- **Manual redeploy.** Render → **Manual Deploy** → **Deploy a specific commit** to pick a prior commit; or fire the hook yourself with `curl -fsS -X POST "${RENDER_DEPLOY_HOOK_URL}&ref=<good-sha>"` (drop `&ref=…` to redeploy `main`'s current head, for example after fixing a dashboard env var).

Story content is not part of a deploy. Published stories live in R2 and roll back through the publish pipeline, not through Render.

### Preview environments (AI-464)

`render.yaml` turns on Render **preview environments**: every pull request gets its own short-lived copy of the Blueprint on a temporary `onrender.com` URL. The preview is rebuilt on each push and deleted when the PR merges or closes, or after 3 idle days (`expireAfterDays`). Render posts the URL on the PR, so a change can be opened on a phone before it merges.

**A preview is read-only by construction.** It must never publish to the live bucket, write a family's data, or spend generation credit. `previewValue` overrides in `render.yaml` apply only to previews; the production values set in the dashboard are untouched.

| Variable | In a preview | Why |
|----------|--------------|-----|
| `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL` | empty | With Clerk unset, `/parent` and `/workshop` answer **404**, which closes every write path |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `OPENROUTER_API_KEY` | `preview-disabled` | A second lock: nothing can publish or generate even if a route slips past |
| `R2_ENDPOINT_URL` | empty | No R2 client can reach the live bucket, and the R2 config check (which requires a separate pending bucket) is skipped, so a preview boots either way |
| `ASSET_BASE` | `/static/content` | Same-origin dev fixtures. The R2 CORS policy (`deploy/r2-cors.json`) lists exact origins, so a preview host could not fetch published stories |

`tests/test_render_previews.py` builds settings from these `previewValue`s and asserts the result: landing, player and `/health` answer 200; the parent area and workshop answer 404.

**What a preview shows:** the landing page, the child player, and the settings sheet, all against the fixture shelf. **What it doesn't:** the parent area and workshop (Clerk-gated), and real published art. Review those locally, or on production after merge.

**Turning it on:** preview environments are a Render workspace feature. **Unverified:** whether it needs a paid workspace tier (Render's pricing page decides this). If the Blueprint sync reports previews as unavailable, enable them in **Blueprint → Settings → Preview environments**, or upgrade the workspace. Each live preview bills as its own Starter instance while it exists; the 3-day expiry bounds that cost.

---

## 4. Clerk (parent + workshop sign-in, ADR-003)

Both the parent area **and** the workshop (`/workshop`, AI-426) authenticate
through Clerk — there is no separate operator secret anymore. The player never
loads Clerk JS; the server verifies session JWTs locally via JWKS — the only
REST call to Clerk in the whole codebase is the one-time family-token write at
first sign-in (`src/api/clerk.py`). With `CLERK_PUBLISHABLE_KEY` or
`CLERK_JWKS_URL` unset, both `/parent` and `/workshop` answer **404** and do
not exist.

### 1. Create the application

1. [dashboard.clerk.com](https://dashboard.clerk.com) → **Create application**.
2. Sign-in options: enable **Email** with **magic link** (passwordless).
   Optionally enable **Google** OAuth.
3. Note the **Publishable key** and **Secret key** from **API keys**.
   The **Frontend API URL** on the same page gives you the other two values:
   - JWKS URL: `https://<frontend-api>/.well-known/jwks.json`
   - Issuer: `https://<frontend-api>`

### 2. Session token template (custom claims)

Dashboard → **Sessions** → **Customize session token** → Claims editor:

```json
{
  "role": "{{user.public_metadata.role}}",
  "family_token": "{{user.public_metadata.family_token}}",
  "disabled": "{{user.public_metadata.disabled}}"
}
```

Save. Individual fields — not the whole `public_metadata` object — keep the
session token under Clerk's 1.2 KB limit. Until a user is provisioned these
claims resolve to null, which the server treats as "not provisioned yet"
(and `disabled: null` as not disabled). The `role` claim is what the workshop
reads to decide operator vs. parent (see **Operators** below) — the server
resolves scope straight from the verified JWT, with no per-request Clerk call.

### Operators

The workshop admits any signed-in user whose Clerk `public_metadata` contains
`{ "role": "operator" }`. Set it on your own user in the Clerk dashboard
(Users → your user → Metadata → Public). Everyone else is treated as a parent
and sees a "coming soon" page until the parent workshop views ship. There is
no allow-list and no env flag — the role claim is the whole operator model.

### 3. Bot protection

Dashboard → **Attack protection** → enable **Bot sign-up protection**.
Sign-up now guards a wallet (story generation costs money), so this is
required, not optional. Suspected bots get an interactive challenge; if we
later build a custom sign-up form it must include the
`<div id="clerk-captcha">` placeholder element.

### 4. Environment variables (Render → Environment)

| Variable | Value |
| -- | -- |
| `CLERK_PUBLISHABLE_KEY` | `pk_…` from API keys |
| `CLERK_SECRET_KEY` | `sk_…` from API keys |
| `CLERK_JWKS_URL` | `https://<frontend-api>/.well-known/jwks.json` |
| `CLERK_ISSUER` | `https://<frontend-api>` |

Leaving `CLERK_PUBLISHABLE_KEY` or `CLERK_JWKS_URL` unset disables both the
parent surface **and** the workshop (routes 404) — the safe default for
deploys that don't want Clerk yet. The workshop also needs
`CLERK_PUBLISHABLE_KEY` set because ClerkJS mounts in the browser to keep the
`__session` JWT refreshed for HTMX polling.

### 5. Verify

Sign in at `/parent` (once the pages land — until then, any Clerk-hosted
account page works for template testing), then decode the `__session`
cookie at jwt.io: it must carry `role`, `family_token`, and `disabled` claims
(null before first provision). For the workshop, set your own user's
`public_metadata.role` to `operator` and open `/workshop`: you should reach
the dashboard rather than the "coming soon" page.

---

## Sentry (optional error monitoring, ADR-009)

1. Create a Sentry project (platform: **FastAPI**) in an **EU-region**
   organization, so the DSN host is `*.ingest.de.sentry.io`.
2. Set `SENTRY_DSN` in Render → Environment. `SENTRY_ENVIRONMENT` is already
   set by `render.yaml` (`production`, or `preview` on PR previews), and the
   release comes from Render's `RENDER_GIT_COMMIT` automatically.
3. Leave `SENTRY_DSN` unset to turn Sentry off entirely — the SDK never initializes.

Sentry is server-side only. Events carry no request bodies, no stack-frame
locals, no IPs, and filtered auth/cookie headers; failed workshop and parent
runs are reported even though the run itself lands `failed` gracefully.

---

## Application logs (B6, AI-485)

The app writes one line per event to stdout; `PYTHONUNBUFFERED=1` in the
`Dockerfile` makes each line reach Render at once.

- **Level:** `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`;
  default `INFO`) sets the `src.*` loggers. Third-party libraries stay at
  `WARNING`. Leave it unset in Render unless you are debugging.
- **Format:** key=value (logfmt) lines, not JSON:
  `ts=2026-10-03T17:30:51.466Z level=INFO logger=src.workshop.manager event=run_started run_id=… family=…`.
  Render's log viewer shows raw text and searches by substring, so
  `event=run_failed` or `run_id=<id>` finds every line for an event or a run.
  JSON would be harder to read there and gives no extra filtering. A failure's
  traceback follows on the next lines.
- **Where to read them:** Render → the `cantastorie` service → **Logs**. Search
  `run_id=<id>` (shown in the workshop URL) to follow a single run from
  `run_submitted` through each `step_finished` (with `duration_ms`) to
  `run_staged` or `run_failed`. Every line emitted inside a run carries its
  `run_id`. uvicorn's access lines keep their format, but any family token in
  a request path (`/published/families/<token>/…`, `family_token=<token>`) is
  replaced by its hash before the line is written. The CLI (`generate`,
  `publish`, `audit`) logs the same lines.
- **Privacy:** a family token is logged only as `family=<12 hex>`, a salted
  sha256 prefix that stays the same across deploys. Story text, premises, API
  keys and request bodies are never logged. Neither is a safety judge's free
  text: a text- or image-gate rejection logs only the failed criterion names
  (e.g. `criteria=safety/no_brands`), counts, `run_id` and the exception class,
  with no traceback (content-limit violations too). Unexpected errors keep
  their traceback frames, but each exception in the chain is shown by type
  name only, never its message, since a message can quote model output. The
  judge's reasons and full exception messages still reach the workshop's run
  page and Sentry (unchanged); only stdout leaves them out.
- **Sentry:** log records reach Sentry as breadcrumbs only. A third-party
  library's `ERROR` log no longer becomes a Sentry event on its own; only an
  explicit `capture_exception` (or an unhandled exception) files one.

---

## 5. Verify (the AI-365 acceptance)

On a phone on **cellular** (not home wifi), open the Render URL and confirm:

- [ ] A full story night plays end to end — tap the shelf (greeting), tap the cover, narration and pages run hands-free to the end screen; replay works.
- [ ] **No cookies** are set (DevTools → Application → Cookies, or remote-debug the phone).
- [ ] The page shell loads from the Render origin; **manifest and story assets load from the R2 domain** (Network tab).
- [ ] R2 **access logging/Logpush is off** (Dashboard check from step 1).

---

## Cost

- **Render Starter**: ~$7/month, always-on (the cold-start decision — a bedtime app is opened cold nightly, and the free tier's spin-down would blow the 4-second first-open budget).
- **Preview environments**: one extra Starter instance per open PR, prorated, and deleted after merge, close or 3 idle days.
- **R2**: zero egress fees; storage for the launch library is pennies.
- **Spoken prompts**: a one-off of about 35 TTS calls for the seven languages without them (unverified estimate: well under US$1). Reruns are free (see [Spoken prompts for every language](#spoken-prompts-for-every-language-h6-ai-481)).
