# Design Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the shipped Cantastorie product up to the high-fidelity design in `design_handoff/README.md` — orchid accent, portrait covers, rebuilt child SPA (shelf/settings/gate), rebuilt parent workshop, and i18n cleanup.

**Architecture:** Rebuild the handoff reference in the repo's own stack — semantic vars in `tokens.css`, `screens.js` `el()` helper + `player.css` for the child SPA, Jinja2+HTMX for parent/workshop, Python pipeline for covers. Sequenced by dependency: tokens and the cover-schema change land first because everything downstream consumes them.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 / HTMX (backend + workshop), vanilla JS `el()` SPA + Tailwind-free hand-written CSS (child player), Pydantic (pipeline models), Vitest (JS unit), Playwright (E2E), pytest (Python).

**Spec:** `docs/superpowers/specs/2026-09-13-design-overhaul-design.md`

## Global Constraints

- No new hex values outside `tokens.css` — every colour maps to a semantic variable.
- CSS shapes only — no SVG files, no icon font (removes the last `GEAR_SVG`).
- Respect the 2-second HTMX swap on the run-progress fragment — no looping animations.
- Reuse `palette.js` for theme resolution (`data-palette`/`data-theme`, dusk from 19:00, `?theme=`/`?palette=` overrides, `.night` in player). Do not re-implement theming.
- All new child-facing copy localized across all 7 languages: `it, es, en, el, de, bg, ru`.
- Orchid accent is `--accent: #A88BE0`, `--accent-text: #BFA8EB`. White button text stays `#FFFDF7` (the one literal).
- Child frame target 402px; hit targets ≥44px.
- New Story schema field is additive with fallback (`cover` absent → `pages[0].image`) — no data backfill.
- Follow existing file patterns; do not restructure files that aren't part of a task.

---

## Task 1: Orchid accent across all palettes (tokens.css)

**Linear:** AI-442 (Phase 1)

**Files:**
- Modify: `src/static/css/tokens.css` (accent + karaoke definitions across indigo/warm/seaglass/plum × light/dusk + `.night`)
- Test: `tests/e2e/tokens.spec.js` (Playwright, computed-style assertion)

**Interfaces:**
- Produces: `--accent`/`--accent-text`/`--accent-22` resolve to orchid on every `data-palette` × `data-theme` combination. Consumed by Tasks 6–11 (shelf family ring, live-run beads, custom-theme CTA).

- [ ] **Step 1: Write the failing test** — assert orchid resolves for each palette.

```javascript
// tests/e2e/tokens.spec.js
import { test, expect } from '@playwright/test';

const PALETTES = ['indigo', 'warm', 'seaglass', 'plum'];
const THEMES = ['light', 'dusk'];

for (const palette of PALETTES) {
  for (const theme of THEMES) {
    test(`accent is orchid for ${palette}/${theme}`, async ({ page }) => {
      await page.goto(`/?palette=${palette}&theme=${theme}`);
      const accent = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()
      );
      // #A88BE0 in any notation → normalize by comparing rgb
      const probe = await page.evaluate(() => {
        const d = document.createElement('div');
        d.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
        document.body.appendChild(d);
        return getComputedStyle(d).color;
      });
      expect(probe).toBe('rgb(168, 139, 224)'); // #A88BE0
    });
  }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright test tests/e2e/tokens.spec.js`
Expected: FAIL — current `--accent` is honey gold (`#D9A441`/`#E4B75E`/`#E8B44C`/etc.).

- [ ] **Step 3: Replace every `--accent` and `--accent-text` value with orchid**

Current honey-gold `--accent` values to replace (from delta analysis) — indigo light `#D9A441` (L66), indigo dusk `#E4B75E` (L201), indigo `.night` (L420); warm light `#E8B44C` (L113), warm dusk `#E8B75A` (L157), warm `.night` (L408); seaglass light `#E0B15C` (L247), seaglass dusk `#E6C077` (L291), seaglass `.night` (L432); plum light `#E2A93F` (L337), plum dusk `#E8BC5F` (L381), plum `.night` (L444). Set every `--accent: #A88BE0;` and every `--accent-text: #BFA8EB;`. Update `--accent-22` to `rgba(168,139,224,0.22)` (or `#A88BE0` at 22% in the notation the file uses). Update the per-palette `--karaoke` alias (accent at .45, ~L483–497) to `rgba(168,139,224,0.45)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx playwright test tests/e2e/tokens.spec.js`
Expected: PASS for all 8 palette×theme combinations.

- [ ] **Step 5: Grep guard — no stray honey gold in accent role**

Run: `grep -nE '#E4B75E|#D9A441|#E8B44C|#E0B15C|#E2A93F|#E8B75A|#E6C077|#E8BC5F' src/static/css/tokens.css`
Expected: no matches on `--accent*`/`--karaoke` lines (other tokens may legitimately keep warm hues).

- [ ] **Step 6: Commit**

```bash
git add src/static/css/tokens.css tests/e2e/tokens.spec.js
git commit -m "feat(tokens): move --accent to orchid across all palettes (AI-442)"
```

---

## Task 2: Fix warm --rest / --primary collision (tokens.css)

**Linear:** AI-442 (Phase 1)

**Files:**
- Modify: `src/static/css/tokens.css` (warm `--rest` light L122, dusk L166)
- Test: `tests/e2e/tokens.spec.js` (extend)

**Interfaces:**
- Produces: in `data-palette="warm"`, `--rest` ≠ `--primary` in both themes. Consumed by Task 10 (rested-run bead/card).

- [ ] **Step 1: Add failing assertions** — rest must differ from primary in warm.

```javascript
// append to tests/e2e/tokens.spec.js
for (const theme of ['light', 'dusk']) {
  test(`warm --rest differs from --primary (${theme})`, async ({ page }) => {
    await page.goto(`/?palette=warm&theme=${theme}`);
    const [rest, primary] = await page.evaluate(() => {
      const s = getComputedStyle(document.documentElement);
      return [s.getPropertyValue('--rest').trim(), s.getPropertyValue('--primary').trim()];
    });
    expect(rest).not.toBe(primary);
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test tests/e2e/tokens.spec.js -g "warm --rest"`
Expected: FAIL — warm `--rest` and `--primary` are both `#C9714F` (light) / `#D98B66` (dusk).

- [ ] **Step 3: Give warm a distinct `--rest`**

Set warm `--rest` to the dusty-rose used by other palettes: light `#B06A55` is too close to primary; use `#C08087` (light) to match the indigo/plum dusty-rose family, and `#D0989E` (dusk). Keep `--rest-text`/`--rest-16` consistent (derive `--rest-16` as the same hue at 16%). Verify seaglass (`--primary #47858A`/`#7FB5BA`, `--rest #B06A55`/`#C68872`) and plum (`--primary #7B5A8E`/`#A98BC0`, `--rest #A85E68`/`#C08087`) are already collision-free — no change.

- [ ] **Step 4: Run to verify it passes**

Run: `npx playwright test tests/e2e/tokens.spec.js`
Expected: PASS (all accent + warm-rest tests).

- [ ] **Step 5: Commit**

```bash
git add src/static/css/tokens.css tests/e2e/tokens.spec.js
git commit -m "fix(tokens): warm --rest no longer collides with --primary (AI-442)"
```

---

## Task 3: Gate the palette bar behind ?debug=1 (workshop dashboard)

**Linear:** AI-442 (Phase 1)

**Files:**
- Modify: `src/templates/workshop/dashboard.html:96-100` (`.ws-palette-bar` block)
- Modify: the dashboard route handler (passes template context) — add a `debug` flag from `request.query_params.get("debug") == "1"`
- Test: `tests/` (pytest — dashboard renders palette bar only when `?debug=1`)

**Interfaces:**
- Consumes: existing dashboard route + template context.
- Produces: `debug` boolean in the dashboard template context.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workshop_dashboard_debug.py
def test_palette_bar_hidden_by_default(client):
    r = client.get("/workshop")  # adjust to real operator dashboard route
    assert "ws-palette-bar" not in r.text

def test_palette_bar_shown_with_debug(client):
    r = client.get("/workshop?debug=1")
    assert "ws-palette-bar" in r.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_workshop_dashboard_debug.py -v`
Expected: FAIL — palette bar always renders.

- [ ] **Step 3: Wrap the block in `{% if debug %}` and pass `debug` from the route**

In `dashboard.html`, wrap lines 96–100 in `{% if debug %} … {% endif %}`. In the route, add `debug = request.query_params.get("debug") == "1"` to the template context.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_workshop_dashboard_debug.py -v`
Expected: PASS both.

- [ ] **Step 5: Commit**

```bash
git add src/templates/workshop/dashboard.html src/api/routes tests/test_workshop_dashboard_debug.py
git commit -m "fix(workshop): gate palette bar behind ?debug=1 (AI-442)"
```

---

## Task 4: Add `cover` field to Story schema (pipeline model)

**Linear:** AI-443 (Phase 2)

**Files:**
- Modify: `src/pipeline/models.py` (Story model ~L89-98)
- Test: `tests/test_story_model.py`

**Interfaces:**
- Produces: `Story.cover: str | None = None`. Consumed by Tasks 5 (pipeline emit), publish, and Task 6 (JS consumer).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_story_model.py
from src.pipeline.models import Story

def test_story_cover_defaults_none(minimal_story_kwargs):
    s = Story(**minimal_story_kwargs)
    assert s.cover is None

def test_story_cover_roundtrips(minimal_story_kwargs):
    s = Story(**{**minimal_story_kwargs, "cover": "cover.png"})
    assert s.model_dump()["cover"] == "cover.png"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_story_model.py -v`
Expected: FAIL — `cover` not a field.

- [ ] **Step 3: Add the field** — `cover: str | None = None` to `Story`. Bump `schema_version` only if other loaders assert on it (check `story.js` loader tolerance first; the JS reads schema_version 1 — keep 1 since the field is additive/optional).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_story_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/models.py tests/test_story_model.py
git commit -m "feat(pipeline): add optional Story.cover field (AI-443)"
```

---

## Task 5: Emit a portrait cover in the illustrate step + publish it

**Linear:** AI-443 (Phase 2)

**Files:**
- Modify: `src/pipeline/steps/illustrate.py` (add portrait cover generation; ~L243-256 `_cover_prompt`)
- Modify: `src/pipeline/publish.py:195` (write `story.cover` path to manifest instead of reusing `pages[0].image`)
- Test: `tests/test_illustrate_cover.py`, `tests/test_publish_cover.py`

**Interfaces:**
- Consumes: `Story.cover` (Task 4).
- Produces: pipeline sets `story.cover` to the emitted portrait image filename; publish writes `"cover": {public_base}/stories/{id}/{story.cover}` when present, else falls back to `pages[0].image`.

- [ ] **Step 1: Write failing tests** (mock the image model; assert a portrait cover file is requested and `story.cover` is set; assert publish manifest uses it, with fallback).

```python
# tests/test_publish_cover.py
def test_manifest_uses_cover_when_present(story_with_cover, publish_ctx):
    entry = build_manifest_entry(story_with_cover, publish_ctx)  # adjust to real fn
    assert entry["cover"].endswith(f"/{story_with_cover.cover}")

def test_manifest_falls_back_to_first_page(story_without_cover, publish_ctx):
    entry = build_manifest_entry(story_without_cover, publish_ctx)
    assert entry["cover"].endswith(f"/{story_without_cover.pages[0].image}")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_publish_cover.py tests/test_illustrate_cover.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `illustrate.py`, generate a dedicated portrait cover (reuse the character-sheet reference; prompt for portrait framing suitable for a 5/6 tile) and assign `story.cover`. In `publish.py:195`, use `story.cover` when set, else `story.pages[0].image`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_publish_cover.py tests/test_illustrate_cover.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/steps/illustrate.py src/pipeline/publish.py tests/test_illustrate_cover.py tests/test_publish_cover.py
git commit -m "feat(pipeline): emit dedicated portrait cover per story (AI-443)"
```

---

## Task 6: JS consumes `cover` with fallback (story loader + shelf)

**Linear:** AI-443 (Phase 2)

**Files:**
- Modify: `src/static/js/story.js` (loader ~L28-87) and shelf cover derivation in `src/static/js/screens.js`
- Test: `tests/unit/cover.test.js` (Vitest)

**Interfaces:**
- Consumes: manifest `cover` field.
- Produces: `coverSrc(entry)` helper returning `entry.cover ?? entry.pages[0].image`. Consumed by Task 7 (shelf render).

- [ ] **Step 1: Write the failing test**

```javascript
// tests/unit/cover.test.js
import { describe, it, expect } from 'vitest';
import { coverSrc } from '../../src/static/js/story.js';

describe('coverSrc', () => {
  it('prefers cover when present', () => {
    expect(coverSrc({ cover: 'c.png', pages: [{ image: 'p0.png' }] })).toBe('c.png');
  });
  it('falls back to first page image', () => {
    expect(coverSrc({ cover: null, pages: [{ image: 'p0.png' }] })).toBe('p0.png');
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/unit/cover.test.js`
Expected: FAIL — `coverSrc` not exported.

- [ ] **Step 3: Implement + export `coverSrc`** and use it where covers are currently derived from `pages[0].image`.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/unit/cover.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/story.js src/static/js/screens.js tests/unit/cover.test.js
git commit -m "feat(player): consume Story.cover with page fallback (AI-443)"
```

---

## Task 7: Shelf — grid, titles, family ring/star, family-first sort

**Linear:** AI-444 (Phase 3)

**Files:**
- Modify: `src/static/js/screens.js` (`buildShelf`, entry model, sort), `src/static/css/player.css` (cover grid L215-293)
- Test: `tests/unit/shelf.test.js` (Vitest, jsdom)

**Interfaces:**
- Consumes: `coverSrc` (Task 6); entry gains `isFamily: boolean`.
- Produces: `sortShelf(entries)` — family entries first, stable within group. Consumed by Task 8 (bottom row cycling reorders nothing).

- [ ] **Step 1: Write failing tests**

```javascript
// tests/unit/shelf.test.js
import { describe, it, expect } from 'vitest';
import { sortShelf } from '../../src/static/js/screens.js';

describe('sortShelf', () => {
  it('puts family covers first, preserving order within groups', () => {
    const out = sortShelf([
      { id: 'a', isFamily: false }, { id: 'b', isFamily: true },
      { id: 'c', isFamily: false }, { id: 'd', isFamily: true },
    ]);
    expect(out.map(e => e.id)).toEqual(['b', 'd', 'a', 'c']);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/unit/shelf.test.js`
Expected: FAIL — `sortShelf` not exported.

- [ ] **Step 3: Implement `sortShelf` + render changes.** Grid `gap:18px 16px`; caption BELOW the cover, centered, `font:700 14px`; family covers get `.cover--family` (violet ring via `--accent`/`--primary-ring` + a CSS star pseudo-element top-right); shared keep the existing cool `--sticker-ring`. Rotation −2°/2°/1.5°/−1.5°. No text label — visual only.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/unit/shelf.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/screens.js src/static/css/player.css tests/unit/shelf.test.js
git commit -m "feat(shelf): family-first sort, violet ring+star, titles below (AI-444)"
```

---

## Task 8: Shelf — sphere-lit moon + bottom row (language sticker + CSS settings dots)

**Linear:** AI-444 (Phase 3)

**Files:**
- Modify: `src/static/css/player.css` (mascot L106-173, bottom row L387-444), `src/static/js/screens.js` (bottom row, remove `GEAR_SVG` L120-121)
- Test: `tests/unit/shelf.test.js` (extend — language cycling), `tests/e2e/shelf.spec.js` (visual smoke)

**Interfaces:**
- Consumes: `main.js` `LANGS`, greeting/prompt localization.
- Produces: `cycleLanguage(current)` returning next code; tapping the sticker updates greeting, prompt, and titles.

- [ ] **Step 1: Write the failing test** for `cycleLanguage`.

```javascript
// append to tests/unit/shelf.test.js
import { cycleLanguage } from '../../src/static/js/screens.js';
it('cycles through all 7 languages and wraps', () => {
  const order = ['it','es','en','el','de','bg','ru'];
  let cur = 'it'; const seen = [cur];
  for (let i = 0; i < 7; i++) { cur = cycleLanguage(cur); seen.push(cur); }
  expect(seen.slice(0,7)).toEqual(order);
  expect(seen[7]).toBe('it'); // wraps
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/unit/shelf.test.js`
Expected: FAIL — `cycleLanguage` not exported.

- [ ] **Step 3: Implement.** Moon: 58px, `radial-gradient(circle at 34% 30%, …)`, `inset -7px -5px 14px` terminator, three crater discs (CSS), off-centre face, eyes narrow at dusk. Bottom row = exactly two: language sticker (CSS shape, one-tap `cycleLanguage`, updates greeting/prompt/titles) + 48px settings dots (2×2 CSS dots, replaces `GEAR_SVG`). Remove the extra parent-corner link from the bottom row.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/unit/shelf.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/css/player.css src/static/js/screens.js tests/unit/shelf.test.js
git commit -m "feat(shelf): sphere-lit moon, language sticker, CSS settings dots (AI-444)"
```

---

## Task 9: Settings sheet — four tile sections + Read-with-me + localization

**Linear:** AI-444 (Phase 3)

**Files:**
- Modify: `src/static/js/screens.js` (`buildSettingsOverlay` L188-237, add localization table), `src/static/css/player.css` (settings L447-602), player on-screen text rendering
- Test: `tests/unit/settings.test.js` (Vitest)

**Interfaces:**
- Consumes: current language, `palette.js` for Light section.
- Produces: `settingsCopy(lang)` returning localized labels; `readWithMe` persisted flag; player reads it to show/hide `page.text`.

- [ ] **Step 1: Write failing tests** — localization completeness + toggle persistence.

```javascript
// tests/unit/settings.test.js
import { describe, it, expect, beforeEach } from 'vitest';
import { settingsCopy, LANG_CODES } from '../../src/static/js/screens.js';

describe('settingsCopy', () => {
  it('has all sections for every language', () => {
    for (const code of LANG_CODES) {
      const c = settingsCopy(code);
      for (const key of ['languages','light','lightDay','lightAuto','lightEvening','lightHint','grownups','readWithMe','workshop','close'])
        expect(c[key], `${code}.${key}`).toBeTruthy();
    }
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/unit/settings.test.js`
Expected: FAIL — `settingsCopy` not exported.

- [ ] **Step 3: Implement.** Bottom sheet `--card`, `border-radius:30px 30px 0 0`, drag handle, dimmed backdrop, `max-height:88%`. Four sections: (1) Languages flag tiles 2-col, selected `--primary-16` + 2px ring; (2) Light — Day / By itself / Evening tiles + localized hint, wired to `palette.js`; (3) For grown-ups — "Read with me" toggle (persists to localStorage; player shows/hides `page.text`) + "The workshop" with lock glyph (opens gate, Task 11); (4) Close — full-width `--primary` pill. All copy from `settingsCopy(lang)` for all 7 languages. Icons CSS shapes.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/unit/settings.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/screens.js src/static/css/player.css tests/unit/settings.test.js
git commit -m "feat(settings): four tile sections, Read-with-me, full i18n (AI-444)"
```

---

## Task 10: Math gate (localized, wrong-answers-stay-put)

**Linear:** AI-444 (Phase 3)

**Files:**
- Modify: `src/static/js/screens.js` (`buildGate`), `src/static/css/player.css` (gate modal)
- Test: `tests/unit/gate.test.js` (Vitest)

**Interfaces:**
- Consumes: `settingsCopy(lang)` gate strings (add gate keys in Task 9's table: `gateHeading`, `gateWrong`, `gateBack`).
- Produces: `buildGate({lang, onPass})` — sum `7+6`, three options, wrong answer shows `gateWrong` and does not dismiss; correct calls `onPass`.

- [ ] **Step 1: Write the failing test**

```javascript
// tests/unit/gate.test.js
import { describe, it, expect, vi } from 'vitest';
import { gateOptions, checkGate } from '../../src/static/js/screens.js';

describe('gate', () => {
  it('offers three options including the correct sum', () => {
    const { options, answer } = gateOptions(7, 6);
    expect(answer).toBe(13);
    expect(options).toContain(13);
    expect(options).toHaveLength(3);
  });
  it('wrong answer does not pass', () => {
    const onPass = vi.fn();
    checkGate(12, 13, onPass); expect(onPass).not.toHaveBeenCalled();
    checkGate(13, 13, onPass); expect(onPass).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/unit/gate.test.js`
Expected: FAIL.

- [ ] **Step 3: Implement** `gateOptions`, `checkGate`, `buildGate` + modal CSS (not dismissible on wrong). Localized heading/wrong-line/back-link for all 7 languages.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/unit/gate.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/static/js/screens.js src/static/css/player.css tests/unit/gate.test.js
git commit -m "feat(gate): localized grown-up math gate (AI-444)"
```

---

## Task 11: Parent workshop — tab shell + Custom disclosure + 4-min note + cap state

**Linear:** AI-445 (Phase 4)

**Files:**
- Modify: `src/templates/parent/packs.html`, `src/templates/parent/stories.html`, `src/static/css/workshop.css`
- Test: `tests/test_parent_form.py`

**Interfaces:**
- Consumes: existing `/parent/packs` + `/parent/stories` routes; `_enforce_caps` (`manager.py:98-115`), `cap_message`/`cap_active` context.
- Produces: tab partial shared by both templates; `Custom…` reveals a `premise` free-text field.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_parent_form.py
def test_custom_theme_reveals_premise_field(client_authed):
    r = client_authed.get("/parent")
    assert 'data-custom-premise' in r.text  # hidden field container present
def test_four_minute_note_before_button(client_authed):
    r = client_authed.get("/parent")
    assert "about four minutes" in r.text
def test_cap_state_dims_form(client_authed_at_cap):
    r = client_authed_at_cap.get("/parent")
    assert "ws-form--capped" in r.text  # dim+inert class applied
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parent_form.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** Tab shell ("Your stories" → `/parent/stories`, "Being made" → `/parent`). Theme select gains `Custom…` → JS reveals a single "Your story" free-text field posting as `premise` (mirror the operator bench disclosure); three fields total. Add "This takes about four minutes" note before the submit button with permission-to-leave copy. When `cap_message`, render a `--rest` card, add `ws-form--capped` (opacity .45) to the form, and mark submit inert.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parent_form.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/templates/parent/ src/static/css/workshop.css tests/test_parent_form.py
git commit -m "feat(parent): tabs, Custom disclosure, 4-min note, capped form state (AI-445)"
```

---

## Task 12: Parent — filter/sort pills, run labels, approve redirect, delete (no hide), exit

**Linear:** AI-445 (Phase 4)

**Files:**
- Modify: `src/templates/parent/stories.html`, `src/templates/workshop/_progress.html`, `src/api/routes/parent.py` (approve redirect L290), `src/static/css/workshop.css`
- Test: `tests/test_parent_stories.py`, `tests/test_parent_approve_redirect.py`

**Interfaces:**
- Consumes: published stories list (with `family_token` ownership), run states.
- Produces: sort param cycling Newest→A–Z; language filter param; family hard-delete arming affordance (no hide).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_parent_approve_redirect.py
def test_approve_lands_on_your_stories(client_authed, staged_run):
    r = client_authed.post(f"/parent/runs/{staged_run.id}/approve", follow_redirects=False)
    assert r.headers["location"] == "/parent/stories"

# tests/test_parent_stories.py
def test_staged_label_reads_needs_your_eyes(client_authed, staged_run):
    r = client_authed.get("/parent")
    assert "needs your eyes" in r.text
def test_family_story_delete_arms(client_authed, family_story):
    r = client_authed.get("/parent/stories")
    assert "Delete for good?" in r.text  # two-step arming on the destructive delete
def test_delete_rejects_non_owned_story(client_authed, shared_story_id):
    # the delete route already guards ownership; deleting a non-owned id must not succeed
    r = client_authed.post(f"/parent/stories/{shared_story_id}/delete", follow_redirects=False)
    assert r.status_code in (403, 404)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parent_stories.py tests/test_parent_approve_redirect.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** Two 36px pills on Your stories: `All languages ▾` (opens list panel: flag, name, count; pill turns accent when active) + `Sort: Newest ▾` (cycles Newest→A–Z→Hidden last); quiet result count. Change `staged` display label to "needs your eyes" (`_progress.html`, `dashboard.html` chip_labels). Beads: ensure current-bead halo is steady (no `transition`/animation that loops within the 2s swap). Approve redirect `/parent`→`/parent/stories` (in `approve_pack`, currently returns `/parent`). Delete (no hide): family stories get a hard-delete that arms first (`×`→"Delete for good?") on the existing `delete_parent_story` route; NO hide/unhide feature. Shared stories aren't listed in the family view and the delete route already rejects non-owned ids — leave that guard. Add "Child's shelf →" `--confirm` pill to the workshop header. Sort cycles Newest→A–Z only (no Hidden-last).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parent_stories.py tests/test_parent_approve_redirect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/templates/parent/ src/templates/workshop/_progress.html src/api/routes/parent.py src/static/css/workshop.css tests/test_parent_stories.py tests/test_parent_approve_redirect.py
git commit -m "feat(parent): filter/sort, friendly labels, approve redirect, delete + exit (AI-445)"
```

---

## Task 13: i18n — shared language display-name map in Jinja templates

**Linear:** AI-446 (Phase 5)

**Files:**
- Create: a shared language display-name map (Python source of truth, e.g. `src/pipeline/languages.py` `LANGUAGE_NAMES: dict[str,str]`, mirroring `main.js` LANGS)
- Modify: `src/templates/parent/packs.html:38`, `src/templates/parent/stories.html:17`, `src/templates/workshop/dashboard.html:31`, `src/templates/workshop/library.html`; register a Jinja filter/global `language_name`
- Test: `tests/test_language_names.py`

**Interfaces:**
- Consumes: `Language` codes (`it, es, en, el, de, bg, ru`).
- Produces: `language_name(code)` → display name; used in templates as `{{ language | language_name }}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_language_names.py
from src.pipeline.languages import LANGUAGE_NAMES, language_name
def test_all_seven_have_names():
    for code in ["it","es","en","el","de","bg","ru"]:
        assert LANGUAGE_NAMES[code]
def test_language_name_fallbacks_to_code():
    assert language_name("zz") == "zz"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_language_names.py -v`
Expected: FAIL — module/map absent.

- [ ] **Step 3: Implement** `LANGUAGE_NAMES` + `language_name`, register as a Jinja global/filter, and replace raw `{{ language }}` renders in the four templates with the display name.

- [ ] **Step 4: Run to verify it passes + grep guard**

Run: `uv run pytest tests/test_language_names.py -v`
Then: `grep -n '{{ *language *}}' src/templates/parent/*.html src/templates/workshop/*.html`
Expected: PASS; grep shows no raw code renders remain (all via filter).

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/languages.py src/templates/parent/ src/templates/workshop/ tests/test_language_names.py
git commit -m "fix(i18n): render language display names, not raw codes (AI-446)"
```

---

## Task 14: End-to-end flow + per-palette visual pass

**Linear:** AI-441 (parent — integration)

**Files:**
- Create: `tests/e2e/overhaul-flow.spec.js` (Playwright)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the E2E test** covering the README full path.

```javascript
// tests/e2e/overhaul-flow.spec.js
import { test, expect } from '@playwright/test';
test('full flow: shelf → player → settings → gate → workshop → approve', async ({ page }) => {
  await page.goto('/play');
  // shelf: family cover first with star
  await expect(page.locator('.cover--family').first()).toBeVisible();
  // open a cover → player
  await page.locator('.cover').first().click();
  await expect(page.locator('.player')).toBeVisible();
  // settings → gate → workshop link
  await page.locator('[data-settings]').click();
  await expect(page.locator('.settings-panel')).toBeVisible();
  // (gate + workshop steps as wired)
});
```

- [ ] **Step 2: Run it**

Run: `npx playwright test tests/e2e/overhaul-flow.spec.js`
Expected: PASS (adjust selectors to the implemented DOM).

- [ ] **Step 3: Per-palette smoke** — loop the four palettes over the shelf and workshop, asserting no console errors and accent/rest resolve orchid/distinct.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/overhaul-flow.spec.js
git commit -m "test(e2e): full overhaul flow + per-palette pass (AI-441)"
```

---

## Task 15: Update living design-system doc

**Linear:** AI-441 (parent — docs)

**Files:**
- Modify: `docs/design/design-system.md`

- [ ] **Step 1: Update** the design-system doc to reflect orchid accent, the `--rest` warm fix, portrait covers, the four settings sections, run-state labels, and the family hard-delete (no hide) rule. Follow `documentation-conventions`.

- [ ] **Step 2: Commit**

```bash
git add docs/design/design-system.md
git commit -m "docs(design): sync design-system with overhaul (AI-441)"
```

---

## Self-Review notes

- **Spec coverage:** §Phase 1 → Tasks 1–3; §Phase 2 → Tasks 4–6; §Phase 3 → Tasks 7–10; §Phase 4 → Tasks 11–12; §Phase 5 → Task 13; testing → Task 14; living doc → Task 15. Accepted/follow-up items (4-min verification, Clerk sharing, title/art regen) are non-blocking and noted in the spec.
- **Type consistency:** `coverSrc` (Task 6) consumed by Task 7; `settingsCopy`/`LANG_CODES` (Task 9) consumed by Task 10; `cover` field (Task 4) consumed by Tasks 5–6; `language_name` (Task 13) shared filter.
- **Sequencing:** Tasks 1–3 (tokens) and 4–6 (cover) both precede the shelf (7–8). SDD runs implementers sequentially; the dependency order above is the dispatch order.
- **Route names / fixtures** (e.g. operator dashboard route, `client_authed`, `staged_run`) are marked "adjust to real" where the implementer must confirm against the codebase — these are the only deliberately unresolved references and each names exactly what to look up.
