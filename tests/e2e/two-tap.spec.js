// The two-tap acceptance test: cold load → tap the shelf (audio wakes,
// greeting plays) → tap a cover → the story begins. No cookies, and the
// only network traffic is the app's own pages and asset fetches.

import { expect, test } from "@playwright/test";

const FONT_ORIGINS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"];

test("two taps from cold load to a story", async ({ page, context, baseURL }) => {
  const offOrigin = [];
  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== new URL(baseURL).origin && !FONT_ORIGINS.includes(origin)) {
      offOrigin.push(request.url());
    }
  });

  await page.goto("/play?theme=light&speed=600");
  // The dev fixture EN manifest has 3 stories (cosmo-space-cowboy, pip-the-pirate,
  // bruno-birthday-bear). Count 3, not 4.
  await expect(page.locator(".shelf .cover")).toHaveCount(3);

  const start = Date.now();

  // Tap 1: anywhere on the shelf — wakes the AudioContext, greeting plays.
  await page.locator(".greeting").click();
  await page.waitForFunction(() => window.__shell?.engine.unlocked === true);

  // Tap 2: a cover — the story begins.
  await page.locator(".cover").first().click();
  await expect(page.locator(".player")).toBeVisible();
  await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "0");

  expect(Date.now() - start).toBeLessThan(4000);

  // The shelf is manifest-driven, not hardcoded.
  expect(await page.evaluate(() => window.__shell.manifestLoaded)).toBe(true);

  // Privacy: no cookies, no third-party traffic (fonts tracked separately).
  expect(await context.cookies()).toHaveLength(0);
  expect(offOrigin).toEqual([]);
});

// Task 8 of the design overhaul replaced the old .parent-corner element with a
// language sticker (.lang-sticker) and a settings gear (.settings-gear) in the
// shelf bottom row. The parent-corner stub is gone; settings is the grown-up
// surface now. This test verifies the settings gear is present and that tapping
// it stays on the shelf (settings sheet opens but shelf is still the active
// screen beneath it).
test("the settings gear opens the settings sheet and shelf stays underneath", async ({ page }) => {
  await page.goto("/play?theme=light");
  await expect(page.locator(".settings-gear")).toBeVisible();
  await page.locator(".settings-gear").click();
  // The settings sheet opens…
  await expect(page.locator(".settings-sheet")).toBeVisible();
  // …but the shelf is still present in the DOM beneath the overlay.
  await expect(page.locator(".shelf")).toBeAttached();
});
