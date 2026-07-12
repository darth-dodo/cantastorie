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
- **Palettes (user decision, 2026-07-12):** the app moves off the Anthropic-adjacent warm-cream/terracotta as its only look. Four palettes — `warm` (today's), `indigo` ("Moonlit indigo"), `seaglass` ("Sea glass & slate"), `plum` ("Plum & lantern") — selectable via a theme switcher, **default `indigo`**, persisted in `localStorage["cantastorie-palette"]`, `?palette=` override wins and persists. Applies to the whole app: workshop AND child shelf/player.
- Two modes stay orthogonal to palette: light and dusk from 19:00 local, `?theme=light|dusk` overrides. Both decided client-side by a tiny inline head script setting `data-theme` / `data-palette` on `<html>` before first paint; tokens live as CSS vars scoped by those attributes.
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

### Task 2: Palette system — four themes, whole app, switchable

**Files:**
- Rewrite: `src/static/css/tokens.css` (semantic vars × 4 palettes × light/dusk, legacy aliases)
- Create: `src/static/js/palette.js` (tiny: read `?palette=`/localStorage, set `data-palette` + `data-theme`; shared by player and workshop)
- Modify: `src/templates/index.html` (load palette.js in head, before CSS paint matters — keep it inline-small or `<script>` sync in head)
- Test: run existing suites (`npx vitest run`, `uv run pytest`) — no visual regressions in behavior-level tests; a small vitest for the palette resolution function if it's importable.

**The semantic variable set** (every palette × mode defines all of these):
`--surface, --page-glow, --card, --field, --ink, --ink-soft, --ink-faint, --mono-ink, --mono-faint, --ghost, --line-10, --line-12, --card-shadow, --btn-shadow, --primary, --primary-text, --primary-16, --primary-ring, --primary-outline, --confirm, --confirm-text, --confirm-22, --accent, --accent-text, --accent-22, --info, --info-20, --info-text, --info-ring, --rest, --rest-text, --rest-16`

Derivation rules (apply uniformly): `--primary-16` = primary at alpha .16 (dusk .18); `--primary-ring` .45 (dusk .5); `--primary-outline` .5 (dusk .55); `--confirm-22`/`--accent-22` at .22; `--info-20` at .2 (dusk .22); `--info-ring` .4 (dusk .45); `--rest-16` .16 (dusk .18); `--ghost` = ink at .06 (dusk .08); `--line-10` ink .1 (dusk .12); `--line-12` ink .12 (dusk .16); `--mono-faint` ink .4; light `--card-shadow: 0 2px 10px <ink .08>` / dusk `0 4px 14px rgba(0,0,0,.35)`; light `--btn-shadow: 0 2px 8px <ink .12>` / dusk `0 2px 8px rgba(0,0,0,.4)`; `--page-glow` = the palette's glow hue as `radial-gradient(90% 30% at 50% -8%, <glow>, transparent 70%)` at .4 light / .12–.14 dusk.

**Base hues per palette** (light mode, then dusk):

| palette | surface | card | field | ink | ink-soft | ink-faint | mono-ink | primary /-text | confirm /-text | accent /-text | info /-text | rest /-text | glow hue |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| warm (today's, verbatim from the prototype LIGHT string) | #FAF3E7 | #FFFDF7 | #FAF3E7 | #4A3B2E | #9A8977 | #B8A78F | #8A7460 | #C9714F / #B05A3A | #8FA37E / #5E7350 | #E8B44C / #A87A28 | #7FA6A8 / #4E7A7C | = primary | rgba(242,217,160,…) |
| warm dusk (prototype DUSK string, verbatim) | #2A2119 | #3B3022 | #322A1D | #F3E9DA | #9E8D77 | #8A7A66 | #B3A188 | #D98B66 / #E09A76 | #98A583 / #A9B892 | #E8B75A / #E0B36A | #7FA6A8 / #9FC0C1 | = primary | rgba(245,223,174,…) |
| indigo | #F2F4F8 | #FFFFFF | #EEF1F6 | #2F3646 | #7C8598 | #A3ABBC | #6B7488 | #5566A8 / #46549C | #5E8A72 / #4C7560 | #D9A441 / #9A7326 | #6C93B8 / #4A6E93 | #A85E68 / #96525C | rgba(213,222,240,…) |
| indigo dusk | #1C2130 | #2A3044 | #232940 | #E8EBF2 | #97A0B5 | #7C86A0 | #AAB3C8 | #8B9AD6 / #A8B4E4 | #7FA98D / #9FC4AC | #E4B75E / #E5C078 | #7FA0C4 / #A9C4DE | #C08087 / #D0959C | rgba(228,183,94,…) |
| seaglass | #EFF4F3 | #FDFFFE | #EAF1F0 | #2E3B3C | #7B8C8C | #A2B1B0 | #647877 | #47858A / #3D7377 | #6D9B6F / #59825B | #E0B15C / #9C752B | #66889E / #4C6B80 | #B06A55 / #9E5B47 | rgba(196,224,220,…) |
| seaglass dusk | #172426 | #24363A | #1E2E31 | #E2EDEB | #8FA6A3 | #74908C | #A8BFBB | #7FB5BA / #98CBD0 | #8FB591 / #ABCBAD | #E6C077 / #E7CA8C | #83A3B8 / #ABC4D4 | #C68872 / #D49C88 | rgba(230,192,119,…) |
| plum | #F5F1F6 | #FFFEFF | #F0EAF2 | #3A3142 | #857B91 | #ACA3B7 | #6F6480 | #7B5A8E / #6B4C7D | #6F9078 / #5C7B64 | #E2A93F / #9C7223 | #8A7FB8 / #6A5F98 | #A85E68 / #96525C | rgba(224,206,232,…) |
| plum dusk | #241D2E | #352C44 | #2C2438 | #EFE9F4 | #9C90AB | #837791 | #B4A8C4 | #A98BC0 / #BEA3D4 | #8FAE97 / #ACC8B3 | #E8BC5F / #EAC77A | #A79BD0 / #C0B6E0 | #C08087 / #D0959C | rgba(232,188,95,…) |

**Legacy aliases** (defined once, so `player.css` and the shelf need no rewrite): `--terracotta: var(--primary); --sage: var(--confirm); --honey: var(--accent); --sea: var(--info); --sticker: var(--card); --moonlight: var(--accent-text); --greeting-sub: var(--ink-soft); --karaoke:` accent at .45; `--prompt: var(--accent-text)` (dusk). Keep `--font-app/--font-story/--app-max`/motion/shape vars untouched. The `.night` block (player always at dusk) re-points to the active palette's **dusk** values — scope it per palette (`[data-palette="indigo"] .night { … }` etc.).

**Selection contract:** `<html data-palette="…" data-theme="…">`. `palette.js` (a few lines, no framework): palette = `?palette=` param if valid (persist it) else `localStorage["cantastorie-palette"]` else `"indigo"`; theme = `?theme=` if `light|dusk` else dusk when local hour ≥ 19. Sets both attributes synchronously at parse time (script tag in `<head>`, not deferred, to avoid a flash). `:root` fallback values (no-JS) = indigo light. Wire into `index.html` now; the workshop base template adopts it in the next task. Expose `window.cantastoriePalette.set(name)` for the switcher UI.

- [ ] Step 1: rewrite tokens.css + palette.js + index.html wiring.
- [ ] Step 2: verify — `npx vitest run` and `uv run pytest` green; `npx playwright test` (e2e) still green; eyeball the shelf at `?palette=indigo|warm|seaglass|plum` and `?theme=dusk` via `make dev` — beads/buttons/text re-color, washes unchanged, no unreadable text. Note anything in player.css that stays hardcoded-warm as acceptable drift in the commit message.
- [ ] Step 3: commit.

### Task 3: The five screens — templates, CSS, JS

**Files:**
- Modify: all of `src/templates/workshop/` (`base.html`, `login.html`, `dashboard.html`, `run.html`, `_progress.html`, `story.html`)
- Rewrite: `src/static/css/workshop.css`
- Create: `src/static/js/workshop.js` (stepper, armed delete, audio pill; ~100 lines vanilla)
- Modify: `src/api/routes/workshop.py` (template context only: staged-story summaries, review-page run context, relative-time helper)
- Test: `tests/workshop/test_routes.py` (update selectors/copy where templates changed; add context assertions)

**Binding design values** (from the prototype; consult `template-body.html` for anything not repeated here):

Colors come from Task 2's semantic tokens — **never hardcode palette hexes in workshop.css**. Wherever this spec or the prototype markup says `--terra`, `--sage`, `--honey`, `--sea-*`, read `--primary`, `--confirm`, `--accent`, `--info-*` — except rested/reject/armed-delete contexts, which use `--rest` (`--rest-text`, `--rest-16`). Fixed white-on-pill text `#FFFDF7` may stay literal. The workshop base template loads `palette.js` (Task 2) the same way `index.html` does, plus the Baloo 2 + Literata Google Fonts link.

Additional bench element: a quiet **palette switcher** row at the bottom of the bench — four 28px dots (each palette's `--primary`, current one ring-highlighted, ≥44px tap targets) calling `window.cantastoriePalette.set(name)`; label "palette" in the mono footnote style.

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

### Task 4: After screenshots + docs

**Files:**
- Modify: `docs/design/design-system.md` (the workshop section: retire "deliberately plain", describe the shipped design; NEW top-level "Palettes" section documenting the four palettes, the switcher, default indigo, and the selection contract)
- Replace: `docs/design/journey/01-shelf-light.png`, `02-shelf-dusk.png` (shelf in the new default indigo), `08-workshop-login.png`, `09-workshop-dashboard.png`, `10-workshop-run.png`, `11-workshop-review.png`; add `12-workshop-rested.png`

Use the scratchpad harness from Task 0 to serve the app with seeded states and capture 402×874 shots (login, bench with all chips, run running, run staged, rested, review — plus the shelf light/dusk) into the scratchpad `after/` dir; copy the journey shots into docs. Also capture one bench shot per palette (warm/indigo/seaglass/plum) for the before/after comparison. Update the design-system doc's palette + workshop paragraphs to match reality (beads, chips incl. rested, delete arming, review pills, switcher). Commit.

- [ ] Step 1: after screenshots captured.
- [ ] Step 2: docs updated, committed.
