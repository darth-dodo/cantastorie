# Release-Readiness Audit

**Date**: 2026-09-13
**Branch**: `docs/release-readiness-audit`
**Base**: `main` @ `722eefc` (post language-roster merge, #91)
**Method**: five parallel specialist review streams — security/tenancy, operations/cost, child-safety/content, frontend quality, code-quality/docs — with every finding below re-verified against the code by hand
**Scope**: `src/`, `tests/`, `docs/`, `Dockerfile`, `render.yaml`, `.github/workflows/`
**Out of scope**: the code-quality and documentation-drift sweep — that stream had not reported when this document was written (see [Coverage gaps](#coverage-gaps))

---

## Verdict

**No-go for general release**, on four independent grounds:

1. A single unset environment variable publishes unreviewed children's content and every family's tenancy secret to a world-readable bucket — and the control the documentation names as the safety net for exactly this does not exist in code.
2. The family lane's approve button publishes to a child with no human having seen the story, directly beneath UI copy asserting the opposite.
3. Generated images — the part a pre-reader actually consumes — are never checked, by a safety rule that is structurally incapable of checking them.
4. Three ordinary bedtime events — closing the tab mid-story, a slow-but-alive network, and the device going to sleep — each leave the child stuck, silent, or in a story that does not exist. The default language ships zero spoken prompts, so every failure message falls back to text a four-year-old cannot read.

None of these is a deep design flaw. The tenancy model, the safety gate, the publish lanes, and the audio engine are well built and hold up under adversarial reading. What is missing is the operational envelope around them, the handling of ordinary interruption, and two places where the interface promises a protection the code does not implement.

The engineering core is sound. The release surface is not.

---

## Baseline health

Measured directly on `722eefc`, not reported second-hand:

| Signal | Result |
|--------|--------|
| `pytest` (excluding e2e) | **323 passed** |
| `vitest` | **143 passed** |
| `make check` (ruff lint + format + mypy) | clean |
| Python line coverage | **92%** |

Coverage soft spots: `src/api/routes/published.py` 40%, `src/api/routes/_nav.py` 64%, `src/api/routes/workshop.py` 82%, `src/workshop/manager.py` 87%.

This is a healthy baseline. Every blocker below is a gap in what the tests *cover*, not a failure among the tests that exist.

---

## Blockers

### B1 — `pending/` falls back to the public bucket, and the audit that documents itself as catching this does not

```
src/config.py:100         r2_pending_bucket: str = ""
src/config.py:111-113     return self.r2_pending_bucket or self.r2_bucket
src/config.py:115         validator covers five R2 fields — omits r2_pending_bucket
render.yaml               contains no R2 environment variables at all
src/pipeline/publish.py:554,606,686,722,737   audit lists only Prefix="published/"
docs/setup.md:51          "The audit script (AI-390) fails on any pending/ object found in the public bucket."
```

`pending_bucket` silently resolves to the public bucket when `R2_PENDING_BUCKET` is unset. `render.yaml` declares no R2 variables, so the value exists only as a hand-entered Render dashboard field with nothing in code to notice its absence.

What lands there if it is missing:

- `pending/staged/{story-id}/` — every story that has **not** passed operator or parent review, with its audio and images.
- `pending/{family_token}/runs/{run_id}.json` — run records keyed by, and containing, the family token.

The family token is a bearer credential: it grants read access to a family's private overlay and, via `/parent/api/provision`, permanent account linkage. Exposure is therefore cross-tenant, not incidental.

The keys are enumerable rather than guessable. `derive_story_id` (`src/pipeline/steps/write.py:175-184`) hashes only theme, language, writer model and prompt version — all public values — so the id space for premise-free stories is computable offline.

`docs/setup.md:51` states the audit fails on any `pending/` object in the public bucket. It does not. `audit_published_bucket` paginates `Prefix="published/"` exclusively; its only `pending/` test is a substring check on manifest entry URLs (`src/pipeline/publish.py:465-466`). A public bucket full of `pending/` objects passes the audit and CI reports green.

**Fix**: reject `pending_bucket == r2_bucket` in the config validator whenever `r2_endpoint_url` is set; add a real `Prefix="pending/"` sweep of the public bucket to `audit_published_bucket`, with a test that plants such an object; add `R2_PENDING_BUCKET` to `render.yaml`.

---

### B2 — Parents approve without ever seeing the story

```
src/api/routes/parent.py:258        "staged_stories": [],
src/api/routes/workshop.py:305      staged_stories = _staged_story_summaries(record.story_ids, settings)
src/templates/workshop/_progress.html:52-53   "Staged — nothing reaches a child unseen"
                                              "every page below is yours to hear and see first"
src/templates/workshop/_progress.html:73-77   non-operator branch: a submit button, nothing else
```

The operator lane computes real story summaries and renders pages, images and audio at `/workshop/staged/{id}`. The parent lane hardcodes the review list empty and has no equivalent route — the parent surface exposes only `GET ""`, `GET /stories`, `GET /packs/{id}/progress` and three POSTs. `/workshop/staged/{id}` redirects non-operators (`src/api/routes/workshop.py:468-469`).

So the family lane's approve control renders with no story content at all, immediately below copy promising the parent that every page is theirs to hear and see first. For a parent there is no "below".

The practical effect is that every family-lane story reaching a child is vetted by an LLM judge alone, with a human's click attached to it — the appearance of review without the substance. This is the product's central safety claim, and it is false for one of the two lanes.

**Fix**: add a family-scoped staged-story view (the operator's `workshop/story.html` can be reused, scoped by `ctx.family_token` through the run record) and gate the approve button on it. Until that exists, remove the "nothing reaches a child unseen" copy from the parent lane.

---

### B3 — A live `/parent` link sits on the child's shelf

```
src/static/js/screens.js:112-114    const parent = el("a", "parent-corner");
                                    parent.href = "/parent";
src/static/css/player.css:603-616   44×44px, position absolute, bottom-right
src/templates/parent/packs.html:5-8 "seed the family token into IndexedDB so a
                                     child player opened on this device merges the overlay"
```

The child shelf renders a 44px tap target — the project's stated minimum touch size — linking into the Clerk-authenticated parent area. The same-device assumption is explicit in the design, not hypothetical: the overlay bridge deliberately places the parent's session on the device the child uses.

Behind that link the controls are plain submit buttons: create a story, approve a story to the shelf, delete a story. Combined with B2, an unsupervised child can tap through and approve unreviewed content onto their own shelf, and exhaust the family's daily run cap.

**Fix**: remove the link from the child shelf, or gate it behind the adult-intent gesture already used for settings. A visible tap target into an authenticated area does not belong on a pre-reader's screen.

---

### B4 — Images are never checked, by a rule that cannot check them

```
src/pipeline/generate.py:66    story, _report = author_story(...)      # safety runs here
src/pipeline/generate.py:80    illustrations = illustrate_story(...)   # images made here, after
src/pipeline/models.py:84      image: str | None = None
src/pipeline/steps/safety.py:40   "- calm_pictures: any image descriptions contain no text and nothing frightening."
src/pipeline/steps/illustrate.py:98-105   image request carries no provider safety settings
```

`safety_gate` serializes the story with `story.model_dump_json()`. At judge time the pipeline has not reached `illustrate_story`, so `Page.image` is `None` on every page and the `Story` model carries no image-description field. The judge is shown `"image": null` and asked to rule on picture calmness. `calm_pictures` is a vacuous pass on every nine-rule report.

Meanwhile the image request sets no provider safety parameters and no post-generation check exists. Image safety rests entirely on the wording of `STYLE_PROMPT` — prompt-level hope, which this codebase explicitly rejects elsewhere ("The prompt is hope; content_rules.py is the validation", `src/pipeline/steps/write.py:5`). Approved page prose is interpolated directly into the image prompt (`src/pipeline/steps/illustrate.py:133-138`).

Text can pass all nine rules while the rendered illustration is frightening or text-bearing. In the family lane (B2) no human ever looks at it.

A rule that always passes is worse than no rule, because it reads as coverage.

**Fix**: enable provider safety filters on the image call and add a vision-model pass over each rendered image against `calm_pictures` before assemble. Either give the rule something real to judge, or remove it from the nine.

---

### B5 — `resume_on_boot()` is dead code; every deploy strands paid-for runs

```
src/workshop/manager.py:151   async def resume_on_boot(self) -> list[RunRecord]:
src/api/main.py               no lifespan, no on_event, no startup hook of any kind
render.yaml:29                autoDeploy: true
src/config.py                 run_stale_after_seconds: 1800
```

The only references to `resume_on_boot` are its own definition, one unit test, and `docs/system-overview.md:234`, which tells the reader that "a crash mid-generation always leaves a record `resume_on_boot()` can find". The record is indeed left; nothing ever goes looking for it. The application never calls the function.

Runs execute as FastAPI `BackgroundTasks` inside the request cycle of a process Render restarts on every deploy. A full generation takes minutes; the shutdown grace is seconds. On each push to `main`: the in-flight task is killed, the record stays `running` in R2 (correct, by design), nothing re-enters it, and the record blocks that family's single active-run slot for thirty minutes while the parent's browser polls a dead spinner.

The durability architecture is built and correct — `RunStore` persists `running` before the first step, transitions are validated, records are values. One missing call at startup nullifies it.

Compounding this, `CONTENT_DIR` points at `/tmp` with no Render disk, so the content-addressed cache is discarded on the same deploy that killed the run. The retry re-buys the whole pipeline.

**Fix**: wire `reap_stale()` and a non-blocking `resume_on_boot()` into a FastAPI `lifespan`. `resume_on_boot` awaits each run sequentially as written, so it must be scheduled as a task rather than awaited during startup, or the health check will fail and Render will roll the deploy back in a loop.

---

### B6 — The application has no logging

A search across `src/` for logger calls, `logging.*` level calls and `print(` returns a single result: `src/workshop/records.py:206`, which logs a skipped malformed record.

There is no logging configuration, no structured logging, no correlation id, and no log line for a run starting, a run failing, a provider call, a publish, an unpublish, a reap, or a cap rejection. The exception that fails a run is stringified into a record field (`src/workshop/manager.py:127-128`) and its traceback discarded.

An operator can read `str(error)` in the workshop UI. For `StoryRejectedError` that is informative. For an HTTP error, a boto3 `ClientError`, or anything unexpected, it is one line with no traceback, no step attribution and no timing — and nothing in the Render logs to correlate against. There is no way to answer "how many runs failed today", "what did we spend", or "is the provider degraded".

Every other blocker in this document becomes materially harder to detect and diagnose because of this one.

**Fix**: configure structured logging to stdout at startup, covering run submitted/started/failed-with-traceback/reaped with `run_id`, `family_token` and duration. Set `PYTHONUNBUFFERED=1` in the `Dockerfile`.

---

### B7 — Closing the tab mid-story boots the child into a story that does not exist

```
src/static/js/main.js:364      function render(state) { save(state); ...   # persists screen
src/static/js/storage.js:13    return saved && typeof saved.screen === "string" ? { choices: [], ...saved } : null;
src/static/js/screens.js:11-15 mockView fallback when view is undefined
```

`render()` writes the whole state object, `screen` included, on every transition; `load()` restores it verbatim with no normalization. Closing the tablet while a story is playing — the ordinary way bedtime ends — persists `screen: "player"`.

On next launch `activeStory` is `null`, so the player builds from `mockView`: generic gradient washes, no art, no audio. `playback.hasStory()` is false, so the fallback page timer advances every few seconds to "The End!". A saved `resumeOpen` shows a resume overlay for a story that no longer exists; a saved `audioError` shows the sleeping bird on boot.

The child opens the app and is dropped into a silent, nameless slideshow that ends in about thirty seconds. They never reach the shelf and never choose a story. No error is shown.

The player tests seed `{ screen: "shelf" }` explicitly, with a comment noting it is "the reopen path" — so the real persisted value is never exercised.

**Fix**: normalize on load. Persist progress only and always boot to the shelf; the resume overlay already reconstructs the rest.

---

### B8 — No timeout on any player fetch, so a slow-but-alive network is a permanent blank screen

```
src/static/js/main.js:61-72, :240, :270-291    no AbortController, no AbortSignal
src/static/js/story.js:28-30                   no timeout
src/static/css/player.css:298-314              .cover.loading shimmer — never applied by any JS
```

There is no abort or timeout anywhere in the player's fetch paths. `init()` awaits the manifest before the first `render()`, so `#app` is empty for the entire duration.

The existing `catch` only fires on rejection. A network that accepts the connection and never answers — captive portal, hotel Wi-Fi, a device waking with a half-dead radio — leaves the promise pending forever, so the offline screen is never reached: it runs only when the manifest is `null`.

The same gap exists on the cover tap. A hung `story.json` means the child taps a cover and nothing happens at all — no screen change, no spinner, no error — and repeated taps hit the same cached pending promise, so they are equally inert. A `.cover.loading` shimmer was designed in CSS and never wired to anything.

The failure-state e2e tests use `route.abort()`, which rejects instantly; no test covers a slow or hanging response.

**Fix**: wrap both fetches in `AbortSignal.timeout(...)` — the codebase already uses this pattern for the story-start prompt — falling into the existing offline screen and page-timer fallbacks.

---

### B9 — Narration dies permanently when the device sleeps

```
src/static/js/main.js:331    { capture: true, once: true }
src/static/js/*.js           no visibilitychange, no pagehide, no focus handler anywhere
```

The AudioContext is unlocked by the first pointer event of the page's life and never again. Mobile browsers suspend the context when the tab is backgrounded or the device locks — which is precisely the most likely event in this app's life: the tablet is set down, the screen sleeps, the child picks it back up.

On return the context is suspended, so `start()` schedules nothing audible and `onended` never fires. The narrate promise already resolved, so the error path never runs and the sleeping-bird retry never appears. Nothing re-renders. The story freezes on the page, silent, with no watchdog and no recovery short of a reload.

**Fix**: make unlock idempotent and re-run it on `visibilitychange`; add a narration watchdog that flips to the existing audio-error state if playback position stops advancing.

---

## High

### H1 — Staged content is keyed globally; approved bytes need not be reviewed bytes

```
src/pipeline/publish.py:46      STAGED_PREFIX = "pending/staged"     # no tenant segment
src/pipeline/steps/write.py:175 def derive_story_id(theme, language, settings, premise=None)   # no shape, no tenant
src/pipeline/publish.py:266-267 delete_staged_story(...) then put_object(...)
```

Run *records* are tenant-scoped; the content they point at is not. The story id is a pure function of theme, language, writer model and premise, so two families requesting the same theme and language collide on one prefix — and `stage_story` deletes before it writes.

Family A stages and reviews a story; another tenant's run overwrites those objects; A approves and publishes whatever now sits at the key. Symmetrically, a family's private story can reach the global shared shelf through an operator collision. `RunRecord` stores only `story_ids` — no content hash binds a review to the bytes reviewed.

A warm cache hides this in testing. After a deploy, with `/tmp` cleared, the writer re-runs and produces different prose under the same id.

**Fix**: scope staging by tenant, include `shape` and a per-run nonce in `derive_story_id`, and record the staged content hash on `RunRecord` to verify at approve time.

### H2 — `premise` is unbounded server-side

```
src/workshop/records.py:72              premise: str | None = None      # no max_length
src/templates/parent/packs.html:40      maxlength="300"                 # client-side only
src/templates/workshop/dashboard.html   no maxlength at all
src/pipeline/steps/write.py:213-214     prompt += f"\nFollow this premise closely:\n{premise}"
```

The only length limit is a DOM attribute on one of the two forms. A direct POST bypasses it. The value is concatenated verbatim into the writer prompt, re-sent on every revision and to the safety judge — cost amplification plus prompt-injection surface.

Injection is mitigated on the output side: the cross-family judge is enforced at config load (`src/config.py:131-139`), `theme` and `language` are `Literal` enums, and `content_rules.py` is code rather than prompt. Nothing bounds the input.

**Fix**: `premise: str | None = Field(default=None, max_length=300)` on `PackRequest`.

### H3 — Deploy pipeline is not gated, and production dependencies are unpinned

```
render.yaml:29      autoDeploy: true
Dockerfile:32       COPY uv.lock .
Dockerfile:38       RUN uv pip install --system .
```

Render deploys on push to `main` without waiting for CI, so a merge failing lint, mypy, tests, the security scan or the R2 audit still ships. There is no staging service and no documented rollback.

`uv.lock` is copied and then ignored — `uv pip install --system .` resolves fresh from `pyproject.toml`, whose constraints are lower bounds. CI tests the locked set; production installs something else. `pydantic-ai>=0.4.0` is pre-1.0 and the pipeline depends on its structured-output behaviour.

**Fix**: `autoDeploy: false` with deployment gated on CI success; install from the lockfile.

### H4 — No global spend ceiling

Caps are per-family (`src/workshop/manager.py:98-115`): one active run plus a daily limit. Sign-up is open, so N accounts yield N times the cap, and operator submissions are exempt entirely. There is no global daily run counter and no spend limit enforced in code.

Positively, and worth stating: **no unauthenticated path triggers spend.** Every generation entry point sits behind a verified Clerk JWT. The revise loop is bounded at two revisions.

**Fix**: a hard spend limit on the provider account is the real backstop; add a global daily run counter and alerting.

### H5 — Manifest deletes bypass the concurrency and cache-control discipline

```
src/pipeline/publish.py:227-244   _publish_manifest: IfMatch + Cache-Control, retries
src/pipeline/publish.py:415-420   unpublish_story: neither
scripts/repair_manifests.py:61-66 same omission
```

The manifest is the index of an entire shelf and the code's own docstring calls it "the one volatile file". Unpublish writes it without `IfMatch`, so a delete concurrent with a publish silently discards the publish, and drops the `max-age=60` header — making removal the slowest operation to propagate, which is precisely backwards for a moderation action.

**Fix**: route every manifest write through the existing optimistic-concurrency helper.

### H6 — The default language ships zero spoken prompts

Measured from the shipped content directories:

| Language | Prompts | Stories |
|----------|---------|---------|
| `en` (default) | **0** | 3 |
| `it` | 5 | 5 |
| `mr` | **0** | **0** |

Only `it` has a `prompts/` directory on disk. `pickLang` defaults to `en`, so for the default language the first tap is silent, the story-start fanfare is skipped, the end prompt is skipped, and — most seriously — the audio-failure state renders the sleeping bird with no voice and the unread text "Oh! The story is taking a nap."

The offline prompt is deliberately same-origin (`src/static/js/main.js:245`) and so cannot fall back to R2; it 404s for every language but Italian.

This contradicts the product's non-reader premise directly: every failure state falls back to text a four-year-old cannot read. Marathi was added to the roster in the tip commit with no content and no prompts behind it, so selecting it yields an empty shelf captioned "Which story today?".

**Fix**: ship the prompt set for every language in the selector, or gate the selector to languages that have one.

### H7 — The unauthenticated content proxy has no path validation and no tests

```
src/api/routes/published.py:43   key = f"published/{path}"        # no validation
src/api/routes/workshop.py:507   if "/" in name or ".." in name: raise HTTPException(404)   # authenticated sibling
```

The **unauthenticated** route has *less* path validation than the operator-gated one beside it. `/published/{path:path}` is the route the child player fetches every manifest, story, image and audio file through when `ASSET_BASE=/published`, and no test in the repository exercises it — coverage 40%, with the entire route body among the missing lines.

Whether the storage edge normalizes dot segments is an untested assumption, and because `pending_bucket` falls back to `r2_bucket` (B1), the blast radius in a single-bucket deployment is unapproved staged content. The route also returns `str(exc)` to anonymous callers, buffers whole objects into memory, and discards the `immutable` cache headers that `publish_story` carefully writes.

**Fix**: allowlist the path shape, narrow the exception, re-emit the upstream cache headers, and add tests.

### H8 — Authorization denials are untested on six destructive or paid workshop routes

Coverage on `src/api/routes/workshop.py` shows the non-operator branch missing on lines 280, 353, 355, 376, 397, 412 and 442 — `POST /workshop/runs` (spends money), `/runs/{id}/approve` (publishes to the shared shelf), `/reject`, `/again` (spends money), `/runs/{id}/delete` and `/staged/{id}/delete` (both destructive).

Removing the `if not scope.is_operator` guard from all six would leave the full Python suite green. The pattern *is* tested on the two routes that got a dedicated spec; it was never applied to the older ones.

Relatedly, the path-traversal test at `tests/workshop/test_routes.py:396-404` asserts nothing useful: the HTTP client normalizes the dot segments before sending, so the request 404s at the router and the guard is never reached — confirmed by line 508 appearing in the missing-lines list. Deleting the guard would not fail that test.

**Fix**: one parametrised test posting to each of the six routes as a non-operator and asserting 403; send the encoded form in the traversal test so the guard is actually exercised.

---

## Medium

| ID | Finding | Location |
|----|---------|----------|
| M1 | `/health` is a constant; Render cannot detect a broken deploy and will cut traffic to it | `src/api/main.py:32-34` |
| M2 | Seven Playwright e2e specs exist; no CI job runs them (jobs are lint, typecheck, test, test-js, security, build, audit) | `.github/workflows/ci.yml` |
| M3 | No fail-fast on missing `OPENROUTER_API_KEY`/R2 — the app boots and fails at first submit | `src/config.py:21` |
| M4 | No CSRF defence; protection rests entirely on a `SameSite` attribute set by Clerk, asserted by no test | `src/api/main.py:20-36` |
| M5 | Family token travels in query strings and every family's token renders on the operator library page | `src/templates/workshop/library.html:17,21` |
| M6 | A leaked family token is permanent self-service account takeover via provision; no rate limit, no rotation | `src/api/routes/parent.py:295-324` |
| M7 | Anonymous `/published/{path}` proxy returns raw exception text (leaks R2 account id) and is unbounded, uncached, non-streaming | `src/api/routes/published.py:36-53` |
| M8 | Zero retries on any provider call; one transient 429 fails an entire run | `src/pipeline/providers.py:64`, `src/pipeline/steps/illustrate.py:71` |
| M9 | Parent progress poll never calls `reap_stale()`; only an operator can unstick a family | `src/api/routes/parent.py:240-262` |
| M10 | Cap enforcement is check-then-act; `save()` on a new record is an unconditional PUT | `src/workshop/manager.py:91-96` |
| M11 | Correct only at one worker and one replica; nothing in `Dockerfile` or `render.yaml` enforces that | `src/workshop/manager.py:85` |
| M12 | Repair tooling and orphan detection skip family overlays entirely | `scripts/repair_manifests.py:35`, `src/pipeline/publish.py:712` |
| M13 | No R2 versioning, backup, or manifest-loss recovery path | `render.yaml`, `docs/setup.md` |
| M14 | `.env.example` omits `ASSET_BASE`, `CONTENT_DIR`, `STAGING_DIR`, `PARENT_DAILY_RUN_CAP` | `.env.example` |
| M15 | JWT issuer unpinned by default (`clerk_issuer` defaults empty) and `exp` is verified-if-present, not required | `src/api/auth.py:179-185` |
| M16 | No security response headers anywhere (CSP, X-Frame-Options, nosniff, Referrer-Policy) | `src/` |
| M17 | Italian spoken prompts are story-independent but cached per-story, so they are re-bought every Italian run | `src/pipeline/generate.py:85-92` |
| M18 | Per-run `get_object` on the event loop; the operator dashboard degrades non-linearly with run count | `src/workshop/records.py:196-204` |
| M19 | Decoded audio buffers are never evicted; three stories browsed ≈ 110 MB of live PCM, enough to have the tab killed on a budget tablet | `src/static/js/audio-engine.js:26,125-143` |
| M20 | No visible focus indicator anywhere in the player — `all: unset` on `button` removes it; WCAG 2.4.7 fails outright | `src/static/css/player.css:49-52` |
| M21 | Whole-story prefetch fires ~20 unthrottled requests with no prioritization, starving page 1's narration | `src/static/js/prefetch.js:41-60` |
| M22 | Two different day/night rules; between midnight and 07:00 the theme flashes bright before settling to dusk | `src/static/js/palette.js:25` vs `src/static/js/main.js:53` |
| M23 | Playback instances leak on language switch; a stale instance speaks the end prompt in the previous language | `src/static/js/playback.js:123` |
| M24 | `<html lang>` is hardcoded `en` and never updated, so screen readers voice Greek/Russian/Marathi with an English voice | `src/templates/index.html:2` |
| M25 | Overlays have no dialog semantics, no focus management, no Escape, and there is no live region for page turns | `src/static/js/screens.js:188-237,323-400` |
| M26 | Cover captions fail AA contrast in both themes (≈4.24:1 light, ≈3.49:1 dusk at 11px/600) | `src/static/css/player.css:222-235` |
| M27 | Prev/next chevrons clip off-screen below 336px viewport width — and they are the only escape when narration stalls | `src/static/css/player.css:796-831` |
| M28 | Resumed branching story skips its second branch and cuts to the end screen; latent until the pipeline emits a twice-branching story | `src/static/js/main.js:186-199` |
| M29 | Three duplicated boto3 client builders that differ subtly — one omits the `or None` fallback, so an empty key blocks environment credentials | `src/api/routes/published.py:26` |
| M30 | `WorkshopScope.publish_target` is security-shaped dead code, read by nothing; lane selection is implicit in the router | `src/workshop/scope.py:24` |
| M31 | Auto-continue on the choice overlay is specified and skipped in e2e but never wired, so a hands-off child waits indefinitely | `tests/e2e/branching.spec.js:159` |

---

## Documentation accuracy

Verified claim-by-claim against the code. The documentation is good in substance and stale in specifics; these are the ones that would mislead someone acting on them.

| Claim | Reality |
|-------|---------|
| `docs/setup.md:51` — the audit fails on any `pending/` object in the public bucket | It never lists `pending/` (B1) |
| `docs/setup.md:11` — "the running site needs no secrets" | Generation runs in-process; the container needs the provider key and R2 credentials |
| `Dockerfile:9-11`, `render.yaml:9` — the app needs no API keys | Same contradiction, repeated in the deploy artifacts; `Dockerfile:10` still names a retired provider |
| `docs/system-overview.md:234`, `docs/adr/ADR-005:12,70` — runs resume on startup | `resume_on_boot()` is never called (B5) |
| `docs/architecture.md:88`, `docs/system-overview.md:27,265` — the player is at `/` | The player is at `/play`; `/` is the landing page, which appears in no document |
| `README.md:114`, `docs/architecture.md:105,143-148` — `gloss.py` is a pipeline step | No such file; the steps are write, safety, revise, narrate, illustrate, assemble |
| `README.md:141`, `docs/architecture.md:74`, `docs/setup.md:11` — two further API keys are required | Neither exists in `src/config.py` nor anywhere in `src/` |
| ADR-005 — the operator face is guarded by a shared env-var secret | Superseded in practice by Clerk; the ADR is unmarked and `docs/design/design-system.md` repeats the stale claim |
| Language count | Code has 8; README and architecture say seven; product says five |
| `docs/product.md:92` — parent dashboard "in progress" | Shipped. The status table also omits the landing page, the operator library, and observability |
| `docs/architecture.md:71`, `AGENTS.md:70` — child state is in IndexedDB | `storage.js` is explicit that progress is localStorage; only the family token is in IndexedDB |

Verified clean: every relative markdown link resolves, every documented `make` target, npm script and CLI command exists, the ADR supersession chain is correctly recorded, and `docs/setup.md`'s R2 section is accurate. `docs/system-overview.md` is the most accurate document in the repository — where it and the others disagree, it is the one that is right.

---

## What holds up

Stated plainly, because a release decision needs the positives as much as the defects. Each was checked against code, and where a guard test pins the behaviour it is named.

**Tenancy isolation is real.** The security stream found no critical vulnerability. Every family-scoped read uses the session token rather than user input (`test_cross_tenant_progress_is_404`, `test_a_family_cannot_approve_another_familys_run`). A posted `family_token` field is structurally ignored — `request_pack` has no such parameter (`test_form_cannot_override_family_token`). Parent delete is double-gated on ownership and lane. The two publish lanes cannot cross (`test_overlay_publish_never_touches_the_shared_manifest`). Every unscoped run listing sits behind an operator check.

**The family token regex is enforced at every boundary** where it becomes a key or URL — server publish, lane parsing, lane discovery, and both browser modules — using `fullmatch`, so trailing-newline bypasses fail. Hostile provision inputs (uppercase, traversal, `"operator"`, over-length) are all rejected.

**The safety gate cannot be bypassed for text.** Both gates run on the original and on every revision; rejection raises before narrate, illustrate, assemble or stage. `publish_story` has exactly three call sites, all behind an explicit human action or the local CLI. No auto-publish path exists, and `_TRANSITIONS` admits no `failed → approved` edge.

**The cross-family judge is enforced by the config object**, not by convention — `Settings` refuses to construct when writer and judge share a vendor prefix.

**Content rules are code and fail loudly.** There is no silent-truncation path anywhere in the pipeline; branching stories budget per heard path.

**Secrets discipline is consistent.** Every credential is `SecretStr`; client `__repr__`s are overridden; `ClerkAPIError` carries only a status code; no secret has ever been committed. Only the publishable key reaches the browser.

**The child player is auth-free by construction** — no Clerk script, cookie or SDK, enforced by a template-scanning test. No analytics or third-party JS on `/play`. Story-derived strings use `textContent`; Jinja autoescaping is intact with no `|safe` anywhere.

**Player failure modes degrade gracefully** — manifest failure yields an offline screen served same-origin, audio failure yields a retry affordance, a missing image falls back to its CSS wash. A child does not see a stack trace.

**Publish is idempotent and correctly ordered.** `_publish_manifest` is a textbook optimistic-concurrency loop; unchanged assets upload nothing; the manifest is written last, so a reader never sees an entry whose assets are absent.

**The cross-tenant audit is genuinely thorough** where it does apply — shared→family, family→shared and family-A→family-B URL leakage each have a dedicated test. Its one gap is the stray-object case in B1.

**The audio engine is the strongest code in the repository.** A play-epoch counter correctly discards voices that decode late, pause handles the ducked / still-loading / playing cases separately, a failed load evicts its own cache entry so one flaky request cannot silence a page for the session, and the start prompt races a timeout so a hung fetch frees the story rather than freezing it mute. Fifteen unit tests pin the contract. B9 is a gap in *when* the engine is unlocked, not in the engine.

**Failure-state art direction is thoughtful.** The offline illustration is bundled same-origin precisely so it does not depend on the network it is reacting to. Rapid double-taps on one cover are deduplicated by caching the promise itself, and `prefers-reduced-motion` covers every keyframe animation without exception.

**Code hygiene is unusually disciplined.** Strict mypy with no escape hatches outside three annotated form coercions, zero TODO/FIXME/HACK markers anywhere in `src/`, `tests/` or `scripts/`, no commented-out code, no debug logging, and only eight broad `except` clauses — none of them a silent `except: pass`. The repository is clean for a public release: no secrets, no personal paths, MIT licence present, `detect-secrets` wired into pre-commit.

---

## Pre-launch checklist

Ordered by risk reduction per unit of effort.

- [ ] `R2_PENDING_BUCKET` set to a separate private bucket, present in `render.yaml`, and enforced by a validator that refuses `pending_bucket == r2_bucket` (B1)
- [ ] Audit extended to sweep `Prefix="pending/"` on the public bucket, with a test; run once against the live bucket (B1)
- [ ] `docs/setup.md:51` corrected, or made true by the above (B1)
- [ ] Parent staged-story view added and the approve button gated on it; misleading copy removed until then (B2)
- [ ] `/parent` link removed from the child shelf or placed behind an adult-intent gesture (B3)
- [ ] Provider safety settings enabled on image generation; `calm_pictures` given something real to judge or removed (B4)
- [ ] `reap_stale()` and non-blocking `resume_on_boot()` wired into a FastAPI `lifespan`; `reap_stale()` also called on a parent-reachable path so a family can self-heal (B5, M9)
- [ ] Structured logging to stdout with run lifecycle and tracebacks; `PYTHONUNBUFFERED=1` (B6)
- [ ] Persisted player state normalized on load so a relaunch always reaches the shelf (B7)
- [ ] Timeouts on the manifest and story fetches, with the loading affordance wired (B8)
- [ ] Audio unlock made idempotent and re-run on `visibilitychange`; narration watchdog added (B9)
- [ ] Spoken prompts shipped for every language in the selector, or the selector gated to complete languages (H6)
- [ ] Path allowlist and tests on `/published/{path}` (H7)
- [ ] Parametrised 403 test across the six unguarded workshop routes; traversal test made to exercise its guard (H8)
- [ ] Hard spend limit set on the provider account; bot sign-up protection verified enabled (H4)
- [ ] `premise` capped server-side on `PackRequest` (H2)
- [ ] Docker installs from `uv.lock`; `autoDeploy: false` with deployment gated on CI; rollback rehearsed once (H3)
- [ ] Staging keyed by tenant, with a content hash bound to the approval (H1)
- [ ] All manifest writes routed through the `IfMatch` + `Cache-Control` helper (H5)
- [ ] `--workers 1` and `numInstances: 1` made explicit with the reason, or the family cap made a conditional write (M11)
- [ ] `/health/ready` checking config and R2; `healthCheckPath` repointed (M1)
- [ ] R2 object versioning or nightly manifest snapshots enabled (M13)
- [ ] e2e suite added to CI (M2)

---

## Limits of this audit

All five review streams reported and every finding above was re-verified against the code. Three limits are worth stating so the document is not read as more than it is.

**Nothing was executed against real infrastructure.** No live bucket was inspected, no deploy was performed, no browser session was driven on a physical device. B1's impact is derived from configuration and code, not from observing an exposed object; the production environment may well have `R2_PENDING_BUCKET` set correctly today. The finding is that nothing in code, CI, or the audit tool would tell you if it were not.

**Two frontend risks could not be confirmed without a device** and are recorded as arithmetic rather than observation: the primary play control may sit under mobile Safari's bottom toolbar, and the choice overlay may clip on very short landscape viewports. Both are flagged in the frontend stream with the measurements shown.

**Severity reflects a general-release context** — unsupervised children, real spend, a public repository. Several findings would be reasonable trade-offs in a private beta and are marked no higher than Medium for that reason.

---

## Related documentation

- `docs/setup.md` — deployment, R2 buckets, CORS (contains two claims corrected by this audit)
- `docs/system-overview.md` — the code as built (contains one claim corrected by this audit)
- `docs/adr/` — settled architectural decisions, including ADR-003 (child player privacy) and ADR-005 (workshop and pending bucket)
- `docs/audits/ponytail-review.md` — prior complexity-only audit, 2026-07-12
