# Workshop Design Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the operator face at `/workshop` up to the locked Claude Design prototype ("the room behind the piazza", direction 6a+6c+6e): mobile-first sticker-book screens for login → bench → run beads → rested → review, plus the three lifecycle actions the design shows that the backend does not yet expose (reject, delete-with-arm, run-it-again).

**Design source of truth:** the exported prototype, extracted to
`/private/tmp/claude-501/-Users-abhishek-stuff-ai-adventures-cantastorie/c17159fd-d322-4ade-b730-bfda0d016ae4/scratchpad/proto-src/template-body.html`
(markup + demo state script). Read it before styling anything — exact paddings, radii, fonts, and copy live there. This plan repeats the binding values.

**Architecture:** No new moving parts. Server-rendered Jinja2 + HTMX stays (ADR-004); the redesign is templates + `workshop.css` + one small `workshop.js` (vanilla, no bundler — ADR-001). New backend surface: `RunStore.delete`, `unpublish_story` in the publish module (the only writer to `published/` keeps that monopoly), and three POST routes on the existing router.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 / HTMX, moto for R2 in tests, vanilla JS + plain CSS (tokens.css is the palette source). `uv run pytest`, ruff + mypy via pre-commit.

## Global Constraints

- Server-rendered HTML + HTMX only; no SPA components, no new JS deps, no Tailwind classes in workshop templates (plain CSS in `src/static/css/workshop.css`, tokens from `tokens.css` where they fit).
- Two typefaces only: Baloo 2 for everything the app says; Literata for story text on the review page. Load via the same Google Fonts link `index.html` uses.
- Two modes: light (warm cream) and dusk from 19:00 local, `?theme=light|dusk` overrides for development. Decide dusk client-side with a tiny inline head script that sets `data-theme="dusk"` on `<html>`; tokens live as CSS vars scoped `:root` / `[data-theme="dusk"]`.
- The progress fragment outerHTML-swaps every 2s while live — **steady states only, no looping CSS animations** (they would restart every swap). The current bead gets a static halo.
- Beads, never numbers: six beads labeled `write revise safety narrate illustrate assemble`; settled = sage, current = honey + halo (24px vs 16px), future = faint. Monotone fill: a bead is done if its checkpoint dir exists OR any later step's does (revise may legitimately never run); with record state `staged/approved/rejected` all beads are done. `failed` shows a terracotta ring on the first not-done bead.
- Run-state chip labels: `queued`, `running`, `staged — review`, `approved`, `rejected`, and `failed` renders as **`rested`** (terra tint). Internal state names in code/tests stay unchanged.
- The six run states are the complete vocabulary; `records.py`'s transition table is law — reject is `staged → rejected`, run-again is a **fresh run** (new record, same PackRequest), never a state change on the old one.
- Run deletion **already shipped on main** (#32): `POST /workshop/runs/{run_id}/delete` (400 for live runs, unpublishes approved stories, shared-story protection, HTMX empty-response for inline removal) plus per-story `POST /workshop/staged/{story_id}/delete`. Do not rebuild any of it; the redesign only replaces its `hx-confirm` UI with the armed two-tap ×.
- English-only UI. Operator tap targets ≥ 44px.
- All auth patterns unchanged: session-cookie check on every route, 404 when no secret configured.
- Run tests with `uv run pytest`; JS tests `npx vitest run`. Pre-commit runs ruff + mypy — new code typed and lint-clean. TDD per task (superpowers:test-driven-development).
- Commit style: `feat:`/`chore:` conventional, incremental per task.

---

### Task 1: Backend lifecycle — reject and run-again

(Run deletion and unpublish already landed on main in #32 — this task adds only the two missing actions.)

**Files:**
- Modify: `src/api/routes/workshop.py` (two POST routes)
- Test: `tests/workshop/test_routes.py`

**Interfaces** (both require session auth like existing writes; unauthenticated → redirect to login):
- `POST /workshop/runs/{run_id}/reject` — `record.advance("rejected")`, save, redirect 303 to `/workshop`. Non-staged runs: `InvalidTransition` must not leak as a 500 — answer 400.
- `POST /workshop/runs/{run_id}/again` — build a fresh run via `manager.submit(OPERATOR_TOKEN, record.request)`, schedule `manager.execute` as a background task (same as `start_run`), redirect 303 to the **new** run's page. Any settled record may be re-run.

- [ ] Step 1: failing tests — reject settles a staged run to rejected; reject of a non-staged run → 400; again creates a new record with the same request, executes it in the background, and redirects to its page; both routes redirect/404 without auth; unknown run id → 404. Follow the existing `_Harness` pattern in `tests/workshop/test_routes.py`.
- [ ] Step 2: implement until green. `uv run pytest tests/workshop` passes; ruff + mypy clean.
- [ ] Step 3: commit.

### Task 2: The five screens — templates, CSS, JS

**Files:**
- Modify: all of `src/templates/workshop/` (`base.html`, `login.html`, `dashboard.html`, `run.html`, `_progress.html`, `story.html`)
- Rewrite: `src/static/css/workshop.css`
- Create: `src/static/js/workshop.js` (stepper, armed delete, audio pill; ~100 lines vanilla)
- Modify: `src/api/routes/workshop.py` (template context only: staged-story summaries, review-page run context, relative-time helper)
- Test: `tests/workshop/test_routes.py` (update selectors/copy where templates changed; add context assertions)

**Binding design values** (from the prototype; consult `template-body.html` for anything not repeated here):

CSS custom properties, verbatim:

```
LIGHT  --surface:#FAF3E7;--page-glow:radial-gradient(90% 30% at 50% -8%,rgba(242,217,160,.4),rgba(242,217,160,0) 70%);--card:#FFFDF7;--field:#FAF3E7;--ink:#4A3B2E;--ink-soft:#9A8977;--ink-faint:#B8A78F;--mono-ink:#8A7460;--mono-faint:rgba(74,59,46,.4);--ghost:rgba(74,59,46,.06);--line-10:rgba(74,59,46,.1);--line-12:rgba(74,59,46,.12);--card-shadow:0 2px 10px rgba(74,59,46,.08);--btn-shadow:0 2px 8px rgba(74,59,46,.12);--terra:#C9714F;--terra-text:#B05A3A;--terra-16:rgba(201,113,79,.16);--terra-ring:rgba(201,113,79,.45);--terra-outline:rgba(201,113,79,.5);--sage:#8FA37E;--sage-text:#5E7350;--sage-22:rgba(143,163,126,.22);--honey:#E8B44C;--honey-text:#A87A28;--honey-22:rgba(232,180,76,.22);--sea-20:rgba(127,166,168,.2);--sea-text:#4E7A7C;--sea-ring:rgba(127,166,168,.4)
DUSK   --surface:#2A2119;--page-glow:radial-gradient(90% 30% at 50% -8%,rgba(245,223,174,.14),rgba(245,223,174,0) 70%);--card:#3B3022;--field:#322A1D;--ink:#F3E9DA;--ink-soft:#9E8D77;--ink-faint:#8A7A66;--mono-ink:#B3A188;--mono-faint:rgba(243,233,218,.4);--ghost:rgba(243,233,218,.08);--line-10:rgba(243,233,218,.12);--line-12:rgba(243,233,218,.16);--card-shadow:0 4px 14px rgba(0,0,0,.35);--btn-shadow:0 2px 8px rgba(0,0,0,.4);--terra:#D98B66;--terra-text:#E09A76;--terra-16:rgba(217,139,102,.18);--terra-ring:rgba(217,139,102,.5);--terra-outline:rgba(217,139,102,.55);--sage:#98A583;--sage-text:#A9B892;--sage-22:rgba(152,165,131,.22);--honey:#E8B75A;--honey-text:#E0B36A;--honey-22:rgba(232,183,90,.22);--sea-20:rgba(127,166,168,.22);--sea-text:#9FC0C1;--sea-ring:rgba(127,166,168,.45)
```

Layout: one centered mobile column, `max-width: 540px` (`--app-max`), background `var(--page-glow), var(--surface)`, body font `'Baloo 2', sans-serif`, color `var(--ink)`. Cards `background: var(--card); border-radius: 18–22px; box-shadow: var(--card-shadow)`; fields `height 44–48px; border-radius 14px; background: var(--field); box-shadow: inset 0 0 0 1.5px var(--line-12)`. Primary buttons: 50px pill, `var(--terra)`, white `#FFFDF7` text, `box-shadow: 0 6px 16px rgba(201,113,79,.35)`; press feedback `transform: scale(.97)` via `:active`.

Screen specs:

1. **Login** — centered: wordmark "Workshop" (700 28px) over italic tagline "the room behind the piazza"; a card with label "Secret", the password field, and an "Enter" terra pill; below, a mono footnote (10px `ui-monospace,Menlo,monospace`, `--mono-faint`): "no accounts — with no secret configured, the workshop answers 404 and does not exist". Wrong secret: re-render login with a quiet one-line error above the button (`--terra-text`), status 401 — replace today's JSON 401 with the template response.
2. **Bench** (dashboard) — header row (wordmark + tagline, baseline-aligned); "Start a run" card: Theme select, "Your theme" free-text premise input, Language select, and a Stories stepper (− / count / + as 34px rounded squares flanking the number; hidden `<input name=count>`, bounds 1–3, JS); full-width Generate pill. Below, "Runs" heading and the run cards: title (premise if set, else theme humanized), meta line "Language · N stories · {relative time}" (+ " · stopped at {first missing step}" for failed), state chip, and the quiet delete `×` (40px pill, `--ghost`) that arms to "Sure?" (`--terra`, white) on first tap and deletes on second — plain JS, no persistence, disarm on outside tap. It replaces the current `hx-confirm` dialog but keeps the existing delete plumbing: `hx-post` to the shipped `/workshop/runs/{id}/delete` route, HTMX swap removing the card inline. Same idiom for the per-story delete that #32 added to the review page — restyle, don't remove. Cards link to the run page; a staged run links straight to its review when it has exactly one story. Empty state card: "No runs yet" / "the shelf is waiting for its first story". Delete on queued/running runs: hide the ×.
3. **Run** — back chip `‹` (44px, card bg) + title/meta header; the bead card: six beads (16px circles; current 24px honey with halo `0 0 0 5px rgba(232,180,76,.28), 0 0 22px 2px rgba(232,180,76,.4)`; labels 10px under each) over a state headline + sub-line. Headlines while running, keyed to the current bead: write "Writing the story…", revise "Softening for bedtime…", safety "Checking every page…", narrate "Narrating the pages…", illustrate "Painting the pictures…", assemble "Binding the book…"; sub "the page refreshes itself". Staged: headline "Staged — nothing reaches a child unseen" (`--sea-text`), sub "every page below is yours to hear and see first", card ring `0 0 0 1.5px var(--sea-ring)`, and a full-width sage pill "Review N pages" (page count from the staged story.json) per staged story — multiple stories stack their review buttons. Approved: "On the shelf — published". Rejected: "Rejected — nothing was published". While running, an anticipatory ghost card (55% opacity, sea ring): "Then: review before anyone hears it" / "the Approve button appears here when the run stages". All of this state lives inside the polled `_progress.html` fragment, same hx-get/every 2s/outerHTML contract.
4. **Rested (failed)** — same run page; beads show terra ring `inset 0 0 0 3px var(--terra)` on the resting step; headline "The run rested at {step}" (`--terra-text`), sub "Nothing was published. The full note is below."; the error in a mono note box (`--field` bg, 11px mono, `--mono-ink`); terra pill "Run it again" → POST `/workshop/runs/{id}/again`.
5. **Review** (story page) — back chip to the run page; title + meta "language · shape · N pages · staged"; one card per page: illustration full-bleed on top (staged asset; fall back to a dusk watercolor CSS wash when the image is missing) with a mono caption `p{n} · …` bottom-left and a 30px page-number circle bottom-right; story text in `Literata` (500 16px/1.75); an **audio pill** (field-bg 999px pill: 44px terra play/pause circle, thin progress track with honey fill, `m:ss / m:ss` time label — vanilla JS over a hidden `<audio>`, one instance playing at a time). Sticky footer over a surface-fade gradient: sage pill "Approve & publish" + outlined terra "Reject" — POST to the run's approve/reject routes; footer renders only while the owning run is staged. The review page needs the run: pass `?run={run_id}` from the run page's review buttons, load the record server-side, tolerate its absence (no footer).

Context/route work (template-only concerns, no lifecycle changes): a `staged_stories` summary (id, title, page count) for the run page; `run` record loading on the staged story page; a relative-time helper ("just now", "N min ago", "N h ago", "yesterday", else date) exposed to templates.

- [ ] Step 1: adjust/extend `tests/workshop/test_routes.py` first where behavior is observable server-side (login error is a 401 template not JSON; run page shows "Review" with page count for staged; staged story page shows approve+reject forms only when its run is staged; dashboard orders runs and hides delete affordance for live runs — assert on stable `data-*`/id hooks, not styling). Run red.
- [ ] Step 2: implement templates/CSS/JS until green; `uv run pytest` + `npx vitest run` pass; ruff/mypy clean.
- [ ] Step 3: eyeball every screen once via the local harness (see Task 3's harness — it exists in scratchpad after Task 0) before calling it done; commit.

### Task 3: After screenshots + docs

**Files:**
- Modify: `docs/design/design-system.md` (the workshop section: retire "deliberately plain", describe the shipped design, keep the journey table)
- Replace: `docs/design/journey/08-workshop-login.png`, `09-workshop-dashboard.png`, `10-workshop-run.png`, `11-workshop-review.png` (and add `12-workshop-rested.png`)

Use the scratchpad harness from Task 0 to serve the app with seeded states and capture 402×874 shots (login, bench with all chips, run running, run staged, rested, review) into the scratchpad `after/` dir; copy the four/five journey shots into docs. Update the design-system doc's workshop paragraphs to match reality (beads, chips incl. rested, delete arming, review pills). Commit.

- [ ] Step 1: after screenshots captured.
- [ ] Step 2: docs updated, committed.
