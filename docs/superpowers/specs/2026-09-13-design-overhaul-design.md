# Design overhaul — spec

**Date:** 2026-09-13
**Branch:** `feature/design-overhaul`
**Source:** `design_handoff/` (the "Cantastorie design system" package). The handoff `.dc.html` files are a **design reference**, not code to copy — everything is rebuilt in the repo's own stack.

## Goal

Bring the shipped product up to the high-fidelity design in `design_handoff/README.md`, across three surfaces (child SPA, parent workshop, landing/tokens) plus one pipeline change (portrait covers). Delivered as a single comprehensive effort, sequenced by dependency.

## Settled decisions

These were the design's open questions; resolved with the product owner:

1. **Adopt orchid `--accent`** — move accent off honey gold `#E4B75E` to orchid `#A88BE0` (text `#BFA8EB`) across all four palettes × light/dusk/night, in `tokens.css` only.
2. **Dedicated portrait cover** — the pipeline emits a portrait cover per story; `Story` gains a `cover` field; the shelf/player consume it. Fallback to `pages[0].image` when `cover` is absent so existing stories keep working (no big-bang backfill).
3. **Keep all 7 languages** (`it, es, en, el, de, bg, ru`) — landing's "7" is already correct. Fix the raw-code leak so every language renders as a display name everywhere. No enum/data trim.
4. **Single comprehensive plan**, sequenced by dependency (tokens → cover schema → child SPA → parent workshop → i18n polish).

## Global constraints

- **No new hex outside `tokens.css`.** Every colour maps to a semantic variable.
- **CSS shapes only** — no SVG files, no icon font (replaces the one remaining `GEAR_SVG`).
- **Respect the 2-second HTMX swap** on the run-progress fragment — no looping animations.
- **Reuse `palette.js`** for theme resolution (`data-palette`/`data-theme`, dusk from 19:00, `?theme=`/`?palette=` overrides, `.night` in player).
- **Localize all new child-facing copy** across all 7 languages.
- Target child frame **402px**, hit targets **≥44px**.

---

## Delta summary (current → required)

Already in place (no or low work): the token *schema* (all semantic vars exist), the two-shelf `scope.py` model (shared vs private overlay — no change), `_enforce_caps` logic, run-state backend vocabulary, and most player icons (already CSS shapes).

| Surface | Gap | Type |
| --- | --- | --- |
| Tokens | `--accent` gold→orchid (~25 values incl. `--karaoke`); fix `--rest`/`--primary` warm collision (`#C9714F` light, `#D98B66` dusk); gate `.ws-palette-bar` behind `?debug=1` | CSS |
| Child shelf | sphere-lit moon (terminator shadow + 3 craters), grid `gap:18px 16px`, title below `700 14px`, family violet-ring+star & family-first sort, language sticker, gear SVG→CSS dots | JS+CSS |
| Settings sheet | 2 dropdowns → 4 tile sections (flags / Light / For-grown-ups w/ "Read with me" + workshop lock) + `--primary` pill + drag handle; `border-radius:30px 30px 0 0`; full localization | JS+CSS |
| Math gate | does not exist — localized `7+6`, three options, wrong answers stay put | JS+CSS |
| Parent workshop | tab shell; `Custom…` disclosure + 4-min note; `_enforce_caps` designed state (dim `.45` + inert submit); filter/sort pills; `staged`→"needs your eyes"; approval redirect `/parent`→`/parent/stories`; hide/unhide + family-vs-shared delete rules; "Child's shelf →" exit | Jinja+HTMX+CSS |
| Pipeline/art | no dedicated portrait cover (reuses `pages[0].image`); `Story` has no `cover` field | Pipeline+data |
| i18n | raw language codes leak in parent/workshop Jinja templates | UI |

---

## Build order & component design

### Phase 1 — Foundations (`tokens.css`)

- Replace `--accent`, `--accent-text`, `--accent-22`, and the per-palette `--karaoke` (accent at .45) with orchid `#A88BE0` / text `#BFA8EB` across indigo, warm, seaglass, plum × light/dusk + `.night` overrides. White button text stays `#FFFDF7`.
- Fix the warm `--rest`/`--primary` collision (both `#C9714F` light, `#D98B66` dusk). Give warm a distinct `--rest` (dusty-rose family, e.g. `#C08087` as used elsewhere); verify seaglass/plum are clean (they are).
- Gate `.ws-palette-bar` (four dots in `workshop/dashboard.html`) behind `?debug=1`.

*Everything else consumes these tokens, so this lands first.*

### Phase 2 — Portrait cover (the blocker)

- `Story` model (`pipeline/models.py`) gains `cover: str | None`.
- `illustrate.py` emits a dedicated **portrait** cover per story (distinct from `_cover_prompt` reuse of page art).
- `publish.py:195` writes the real cover path instead of `pages[0].image`; manifest carries it.
- Consumers (`story.js`, shelf cover component) read `cover` with fallback to `pages[0].image`.
- **Titles must match the art** — where art has a baked-in title or depicts a different scene, flag for regeneration (data task, not blocking the component).

*Blocks the shelf, so it's early.*

### Phase 3 — Child SPA (`screens.js` + `player.css`)

- **Shelf:** sphere-lit moon (58px, `radial-gradient(circle at 34% 30%,…)` + `inset -7px -5px 14px` terminator, three crater discs, off-centre face, eyes narrow at dusk); cover grid `gap:18px 16px`, `aspect-ratio:5/6`, blob radius, rotation −2°/2°/1.5°/−1.5°, **title below** centered `700 14px`; **family covers sort first** with violet ring + star (`isFamily` flag on entry), shared keep the cool ring; **bottom row = exactly two** — language sticker (one tap cycles greeting/prompt/titles) + 48px settings dots (CSS shapes, replaces `GEAR_SVG`).
- **Settings sheet:** bottom sheet `--card`, `border-radius:30px 30px 0 0`, drag handle, dimmed backdrop, `max-height:88%`. Sections: (1) Languages flag tiles 2-col, selected `--primary-16` + 2px ring; (2) Light — Day / By itself / Evening + localized hint; (3) For grown-ups — "Read with me" toggle (controls player on-screen text) + "The workshop" with lock glyph behind the gate; (4) Close — full-width `--primary` pill. All copy localized.
- **Read with me:** toggle renders/hides `page.text` in the player; persisted (localStorage/store).
- **Math gate:** localized heading, sum `7+6`, three options, wrong answers say so and stay put; back link. Opens from the workshop row.

### Phase 4 — Parent workshop (`templates/parent/*`, `workshop.css`)

- **Tab shell** over the two existing routes: "Your stories" (`/parent/stories`) and "Being made" (`/parent/packs`).
- **Make a story:** Theme gains `Custom…` → reveals a single "Your story" free-text field (posts as `premise`), replacing the always-visible wish — three fields, not four; same disclosure as operator bench. Add "This takes about four minutes" note before the button, with explicit permission to leave.
- **One story at a time:** `_enforce_caps` gets a designed state — a `--rest` card above the form, form dimmed to `.45`, submit inert. Not a bare error.
- **Filter & sort (Your stories):** two equal-width pills in one fixed 36px row — `All languages ▾` (opens a list panel: flag, name, count per row; pill turns accent when active) and `Sort: Newest ▾` (cycles Newest → A–Z → Hidden last). Quiet result count below. Not chips.
- **Run states:** display labels only — `failed`→"rested", `staged`→"needs your eyes", `approved`→"on the shelf". Beads: settled sage, current accent + steady halo, future faint, rested hollow w/ `--rest` inset ring. No looping animations. "Run it again" starts a fresh run.
- **Review:** page cards (art on top, Literata text, audio pill on p1); sticky footer `Approve & publish` (confirm) + `Reject` (rest); **approving lands on "Your stories"** (redirect `/parent`→`/parent/stories`).
- **Delete vs hide:** family stories — hide, unhide, hard delete (arms first: `×` → "Delete for good?"); shared stories — hide only, inline reason "no delete — you don't own it."
- **Way out:** a `--confirm` pill in the header — "Child's shelf →".

### Phase 5 — i18n cleanup

- Shared language display-name map so `it/es/en/el/de/bg/ru` render as names in parent/workshop Jinja templates (`parent/packs.html`, `parent/stories.html`, `workshop/dashboard.html`, `workshop/library.html`). Child player already does this in `main.js` — export/reuse one source of truth.

---

## Testing

- **Vitest** (`screens.js` logic): gate validation (wrong stays put), family-first sort, language cycling propagation, "Read with me" toggle state + persistence, cover fallback.
- **Playwright E2E:** the full README flow — shelf → tap cover → player (picture choice at p2) → The end → settings → gate → workshop → Make a story (Custom…) → run → review → Approve → story appears on shelf with violet ring + star. Run per palette for the token/collision changes.
- **Python:** new `cover` pipeline step + manifest field; schema fallback when `cover` absent; `_enforce_caps` designed-state response.

## Accepted behaviour / follow-ups (not blocking)

- **4-minute copy** (§6.7): ship the note as specified; add a task to verify it against real Render free-tier pipeline time and revise copy if off.
- **Clerk per-account = shared deletions** (§6.6): intended — two parents on one family token share the library. No code change; documented here as accepted.
- **Title/art mismatches** (§7): regenerate offending covers as a data task alongside Phase 2.

## Out of scope

- No family-to-family sharing / promote-to-shared path (`scope.py` stays as-is).
- No new palettes; indigo remains the default.
- No auth/onboarding changes (night one is already non-empty via the shared shelf).
