# Task AI-456 — DS Consistency Fixes Report

## Fix 1: Orchid page-glow (tokens.css)

**Lines changed:** L124 and L141

Both `--page-glow` values used the pre-overhaul honey-gold `rgba(228,183,94,.12)`.
Changed both to orchid `rgba(168,139,224,.12)` (same .12 alpha, RGB updated to
168,139,224 matching `--accent: #A88BE0` / `--accent-text: #BFA8EB` in the indigo palette).

- L124: indigo light context (`[data-palette="indigo"]` block, near `--rest`)
- L141: indigo dusk context (`[data-palette="indigo"] .night` block)

**Verify:** `grep -n 'rgba(228,183,94' src/static/css/tokens.css` → 0 results ✅

---

## Fix 2: Readable Library pill (dashboard.html)

**Line changed:** L9

The bare `ws-pill` has `color:#FFFDF7` but no background, making it invisible on
a light surface. Added `ws-pill-primary` modifier, which sets:

```css
.ws-pill-primary {
  background: var(--primary);
  box-shadow: 0 6px 16px var(--primary-ring);
}
```

This is the existing modifier used on the Generate button in the same template (L54).
Readable in both light (indigo `--primary: #7380C4`) and dusk (`--primary: #8B9AD6`).
No new hex introduced.

**Choice rationale:** `ws-pill-primary` is the semantic "action" pill; the Library
link is the primary navigation CTA on the dashboard. `ws-pill-confirm` (green) would
imply confirmation semantics; `ws-pill-rest` (muted red) would imply destructive.

---

## Fix 3: Cap vocab friendly label (packs.html)

**Line changed:** L31

Raw `cap_active.state` was rendered directly (e.g. "staged"). Fixed by inline-mapping
with a `_cap_labels` dict that mirrors the `chip_labels` dict already defined at L71:

```jinja
{% set _cap_labels = {"failed": "rested", "staged": "needs your eyes", "approved": "on the shelf"} %}
{{ _cap_labels.get(cap_active.state, cap_active.state) }}
```

Added "approved" → "on the shelf" to cover states beyond what chip_labels covers
(chip_labels at L71 only maps failed/staged). The `.get(state, state)` fallback
ensures unknown future states still render rather than crashing.

No new filter/helper was created; the mapping is co-located with the template logic.

---

## Fix 4: Dead palette JS removed (workshop.js)

**Lines removed:** ~L116–L149 (original numbering)

Removed:
- `updateActiveDot()` function (L119–L128)
- `document.addEventListener("click", ...)` handler for `[data-palette-name]` (L130–L140)
- `updateActiveDot()` call inside `initAll()` (L149)

No other code in workshop.js or workshop templates references `updateActiveDot`.
The palette bar HTML was already removed; these handlers were pure dead code.

---

## Verification Results

| Check | Result |
|---|---|
| `grep -n 'rgba(228,183,94' tokens.css` | 0 matches ✅ |
| `GET /` | 200 ✅ |
| `GET /workshop` | 200 ✅ |
| `uv run pytest -q` | 353 passed, 0 failed ✅ |
| `npx vitest run` | 159 passed (17 test files), 0 failed ✅ |

---

## Concerns

None. All fixes are minimal and non-breaking:
- tokens.css change only affects visual glow colour, no layout impact.
- Library pill modifier uses a pre-existing class, no new CSS.
- Cap label mapping has a safe fallback for unknown states.
- Dead JS removal has no callers; confirmed by grep.
