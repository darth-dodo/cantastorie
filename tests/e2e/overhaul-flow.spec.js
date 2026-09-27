/**
 * E2E: Design-overhaul flow + per-palette smoke
 *
 * Covers the child SPA flow only (/play — no Clerk auth required).
 * The authenticated parent/workshop flow is covered by Python route tests,
 * not this E2E suite.
 *
 * Selectors derived from screens.js (Task 14):
 *   Shelf:    .covers > .cover-card > button.cover  (family: also .cover--family)
 *             button.lang-sticker, button.settings-gear
 *   Settings: div.settings-backdrop > div.settings-sheet
 *             sections: .settings-section (×3: language, light, grownups)
 *             close:    button.settings-close-pill
 *             workshop: button.settings-row--workshop
 *   Gate:     div.gate-backdrop > div.gate-modal
 *             .gate-heading, .gate-equation, .gate-choices > .gate-choice
 *             .gate-wrong, button.gate-back
 *   Player:   div.screen.player  (exit: button.exit, nav: .nav-prev/.nav-next)
 */

import { test, expect } from '@playwright/test';

const PALETTES = ['indigo'];

// ── Helper ──────────────────────────────────────────────────────────────────

/** Resolve a CSS custom property to its computed RGB string via a probe div. */
async function resolveCSSVar(page, varName) {
  return page.evaluate((v) => {
    const d = document.createElement('div');
    d.style.color = getComputedStyle(document.documentElement).getPropertyValue(v);
    document.body.appendChild(d);
    const result = getComputedStyle(d).color;
    d.remove();
    return result;
  }, varName);
}

// ── 1. Full overhaul flow: shelf → player → settings → gate ─────────────────

test('full flow: shelf renders covers', async ({ page }) => {
  await page.goto('/play?lang=en');

  // Shelf is the initial screen — .covers must be present.
  await expect(page.locator('.covers')).toBeVisible();

  // At least one cover card with a .cover button must render.
  await expect(page.locator('.covers .cover-card')).not.toHaveCount(0);
  await expect(page.locator('.covers .cover')).not.toHaveCount(0);
});

test('full flow: family cover star present when family entry exists', async ({ page }) => {
  await page.goto('/play?lang=en');

  // The en manifest has no isFamily entries; assert covers render (not fail).
  // If a family cover is ever present in seed data, .cover--family will exist.
  const familyCovers = page.locator('.cover--family');
  const count = await familyCovers.count();
  if (count > 0) {
    await expect(familyCovers.first()).toBeAttached();
  } else {
    // No family cover in seed data — assert the covers themselves render.
    await expect(page.locator('.cover').first()).toBeVisible();
  }
});

test('full flow: opening a cover shows player screen', async ({ page }) => {
  await page.goto('/play?lang=en');

  await expect(page.locator('.cover').first()).toBeVisible();
  await page.locator('.covers .cover').first().click();

  // Player screen must appear.
  await expect(page.locator('.screen.player')).toBeVisible();

  // Player has exit and nav buttons.
  await expect(page.locator('button[aria-label="back to stories"]')).toBeVisible();
});

test('full flow: player → back → shelf', async ({ page }) => {
  await page.goto('/play?lang=en');
  await expect(page.locator('.cover').first()).toBeVisible();
  await page.locator('.covers .cover').first().click();
  await expect(page.locator('.screen.player')).toBeVisible();

  // Tap exit (back to stories).
  await page.locator('button[aria-label="back to stories"]').click();

  // Shelf returns.
  await expect(page.locator('.covers')).toBeVisible();
});

test('full flow: settings gear opens settings sheet with four sections', async ({ page }) => {
  await page.goto('/play?lang=en');
  await expect(page.locator('button.settings-gear')).toBeVisible();

  await page.locator('button.settings-gear').click();

  // The settings backdrop + sheet must appear.
  await expect(page.locator('.settings-backdrop')).toBeVisible();
  await expect(page.locator('.settings-sheet')).toBeVisible();

  // Section 1: Language (settings-lang-grid with flag tiles).
  await expect(page.locator('.settings-lang-grid')).toBeVisible();

  // Section 2: Light (settings-light-grid with tiles).
  await expect(page.locator('.settings-light-grid')).toBeVisible();

  // Section 3: Grownups — workshop button and read-with-me toggle.
  await expect(page.locator('button.settings-row--workshop')).toBeVisible();
  await expect(page.locator('.settings-toggle')).toBeVisible();

  // Close pill (Section 4 / footer).
  await expect(page.locator('button.settings-close-pill')).toBeVisible();
});

test('full flow: settings close pill dismisses settings sheet', async ({ page }) => {
  await page.goto('/play?lang=en');
  await page.locator('button.settings-gear').click();
  await expect(page.locator('.settings-sheet')).toBeVisible();

  await page.locator('button.settings-close-pill').click();

  await expect(page.locator('.settings-sheet')).not.toBeVisible();
});

test('full flow: language sticker is present and cycles language on tap', async ({ page }) => {
  await page.goto('/play?lang=en');

  // Lang sticker must be visible.
  const sticker = page.locator('button.lang-sticker');
  await expect(sticker).toBeVisible();

  // Read current label text before the tap.
  const labelBefore = await page.locator('.lang-sticker-label').textContent();

  // Tap once to cycle (switchLanguage is async — it fetches the new lang manifest).
  await sticker.click();

  // Wait for the shelf to re-render with the new language. The sticker is rebuilt
  // as part of buildShelf, so wait until the label textContent differs.
  await expect(page.locator('.lang-sticker-label')).not.toHaveText(labelBefore, { timeout: 10_000 });

  // The new label must be a valid 2-letter uppercase code from LANG_LABELS.
  const labelAfter = await page.locator('.lang-sticker-label').textContent();
  expect(labelAfter?.trim()).toMatch(/^[A-ZБГДЕ-ЯЁҢӨҮҰ]{2,3}$/);
});

// ── 2. Gate: wrong answer stays put; correct answer passes ───────────────────

test('gate: wrong answer shows error message; correct answer (13) passes', async ({ page }) => {
  await page.goto('/play?lang=en');

  // Open settings.
  await page.locator('button.settings-gear').click();
  await expect(page.locator('.settings-sheet')).toBeVisible();

  // Tap the workshop row to trigger the math gate.
  await page.locator('button.settings-row--workshop').click();

  // Gate modal must appear.
  await expect(page.locator('.gate-backdrop')).toBeVisible();
  await expect(page.locator('.gate-modal')).toBeVisible();
  await expect(page.locator('.gate-heading')).toBeVisible();
  await expect(page.locator('.gate-equation')).toBeVisible();

  // The equation is always "7 + 6 = ?" → answer is 13.
  // gateOptions(7,6) produces options [13, 12, 14] (deterministic sort in screens.js).
  // Pick a wrong answer first (12).
  const wrongBtn = page.locator('.gate-choice', { hasText: '12' });
  await wrongBtn.click();

  // Wrong-answer message must become visible.
  await expect(page.locator('.gate-wrong--visible')).toBeVisible();

  // Gate modal is still open.
  await expect(page.locator('.gate-modal')).toBeVisible();

  // Now tap the correct answer (13).
  const correctBtn = page.locator('.gate-choice', { hasText: '13' });
  await correctBtn.click();

  // Gate dismisses (the backdrop is removed from the DOM on pass).
  await expect(page.locator('.gate-backdrop')).not.toBeVisible();
});

test('gate: back button dismisses without passing', async ({ page }) => {
  await page.goto('/play?lang=en');
  await page.locator('button.settings-gear').click();
  await page.locator('button.settings-row--workshop').click();
  await expect(page.locator('.gate-modal')).toBeVisible();

  await page.locator('.gate-back').click();

  await expect(page.locator('.gate-backdrop')).not.toBeVisible();
  // Settings sheet should still be visible after gate is dismissed.
  await expect(page.locator('.settings-sheet')).toBeVisible();
});

// Audio autoplay in headless Chromium is blocked by policy.
// The play-pause button and audio engine are present in the DOM, but
// driven by Web Audio unlock (a user gesture) — we skip the playback
// assertion rather than faking a pass.
test.skip('audio: play-pause triggers narration (skipped — headless autoplay blocked)', async ({ page }) => {
  // Would test: page.locator('button.play-pause').click() → audio starts.
});

// ── 3. Per-palette smoke: shelf renders and --accent resolves to orchid ──────

for (const palette of PALETTES) {
  test(`palette smoke: ${palette} — shelf renders, no console errors, --accent is orchid`, async ({ page }) => {
    const consoleErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await page.goto(`/play?palette=${palette}&lang=en`);

    // Shelf must render.
    await expect(page.locator('.covers')).toBeVisible();

    // --accent must resolve to orchid rgb(168, 139, 224) for every palette.
    const accent = await resolveCSSVar(page, '--accent');
    expect(accent).toBe('rgb(168, 139, 224)'); // #A88BE0

    // No console errors during shelf load.
    // (Filter out known benign browser noise, e.g. favicon 404 from test harness.)
    const realErrors = consoleErrors.filter(
      (msg) => !msg.includes('favicon') && !msg.includes('net::ERR_ABORTED'),
    );
    expect(realErrors).toHaveLength(0);
  });
}

test('every shelf cover is a real tap target, art or not', async ({ page }) => {
  // Regression: inside the centred .cover-card column a width-less .cover
  // collapsed to 0×0 when it had no <img> — the shelf showed captions only.
  await page.goto('/play?lang=en');
  await expect(page.locator('.cover').first()).toBeVisible();
  const sizes = await page.locator('.covers .cover').evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { w: r.width, h: r.height };
    })
  );
  expect(sizes.length).toBeGreaterThan(0);
  for (const s of sizes) {
    expect(s.w).toBeGreaterThanOrEqual(48);
    expect(s.h).toBeGreaterThanOrEqual(48);
  }
});
