# Task 1852 — Landing: dark default, step-4 image, family-stories frame

**Branch:** feature/design-overhaul
**Date:** 2026-09-13

---

## CHANGE 1 — Dusk default (AI-451)

**Approach:** A tiny inline `<script>` placed immediately after the `palette.js <script>` in `<head>` (before any CSS link, so before first paint). It reads `new URLSearchParams(location.search).get("theme")`. If the value is neither `"light"` nor `"dusk"` (i.e. no explicit `?theme=` override), it calls `document.documentElement.setAttribute("data-theme", "dusk")`. palette.js runs first and sets a time-based theme; the inline script then unconditionally overrides to dusk for the landing unless the user explicitly passed `?theme=light` or `?theme=dusk`.

**How `?theme=light` still works:** The guard `t !== "light" && t !== "dusk"` means `?theme=light` is never overridden — palette.js already set `data-theme="light"` from the URL param, and the inline script's condition is false, so it leaves the attribute alone.

**`?palette=` unaffected:** The script only touches `data-theme`, never `data-palette`. palette.js `?palette=` handling is completely separate and untouched.

**Global palette.js unchanged:** No modification to `src/static/js/palette.js`. Other pages (player, workshop, parent) continue to use unmodified time-based logic.

---

## CHANGE 2 — Step 4 image (AI-452)

**Art chosen:** `design_handoff/art/nonna-p2.png` → `src/static/img/landing-spot-review.png`.
Reason: nonna-p2 shows a warm illustrated story interior page — exactly what a grown-up reviews before approving. It reads "parent reading a story page" more directly than boat-p1 (which reads as an adventure illustration).

**Implementation:** Replaced `<div class="l-step-art l-step-art-placeholder">` with `<div class="l-step-art" style="background-image:url(/static/img/landing-spot-review.png)">`. The `-placeholder` modifier and its radial-gradient background are gone.

**Verification:** `grep -c "l-step-art-placeholder"` on live server = 0. `curl /static/img/landing-spot-review.png` = 200.

---

## CHANGE 3 — Family-stories 5th frame (AI-452)

**Section placement:** Added after `l-section-how-made`, before `</main>` / footer. Two-column layout on wide screens (copy left, cover right), stacks on mobile ≤600px.

**Copy:** Eyebrow "Your family's stories" + h2 "A shelf full of stories you made together." + lede about starred violet ring + a `l-ghost-btn` "Manage your library →" pointing to `/parent`.

**Art chosen:** `design_handoff/art/animals-cover.png` → `src/static/img/landing-family-cover.png`.
A bright, illustrated cover reads well as "a family story" — characters are child-friendly and cover-shaped, making the family-library metaphor clear.

**Star + ring technique:** Mirrored directly from `player.css` `.cover--family` and `.cover--family::after`:
- `.l-family-cover` uses `box-shadow: 0 0 0 3px var(--accent), var(--card-shadow)` (token-driven violet ring).
- `.l-family-cover::after` uses the same `clip-path: polygon(50% 0%, 61% 35%, 98% 35%, ...)` CSS star with `background: var(--accent)`. No SVG, no hex.

**Responsiveness:** Two-col at ≥600px (flex-direction: row), single-col below. All text uses existing tokens. The "Manage your library →" link has `min-height: 50px` via `.l-ghost-btn` (≥44px touch target).

**Accessibility:** Semantic `<section>` with `aria-labelledby`, `<h2>` heading, descriptive `alt` on the cover image. Section `aria-hidden` only on the visual container, not the image.

**Dusk + light:** All colours are semantic tokens (`--accent`, `--card-shadow`, `--surface`, `--ink`, `--ink-soft`, `--accent-text`). The section renders correctly in both themes.

---

## Curl / verification results

| Check | Result |
|-------|--------|
| `curl / HTTP` | 200 |
| `grep -c l-step-art-placeholder` | 0 |
| `/static/img/landing-spot-review.png` | 200 |
| `/static/img/landing-family-cover.png` | 200 |
| `l-section-family` in HTML | present |
| Dusk override script in `<head>` | present |

---

## Test results

`uv run pytest -q` — **346 passed, 18 warnings** (baseline = 346). No regressions. The landing token test (`test_landing_is_wired_to_tokens`) passes — palette.js and tokens.css still present in HTML.

---

## Deviations

- The coordinator sent two contradictory relay messages during execution (first: "don't copy images, they'll be generated"; second: "go back to copying images"). Final action follows the second correction and the original task spec: images copied from `design_handoff/art/` into `src/static/img/`.

## Concerns

None blocking. The two image files are small watercolor PNGs sourced from design_handoff art; they load fine in both themes. If the image pipeline later replaces them, it can simply overwrite the files at the same paths.
