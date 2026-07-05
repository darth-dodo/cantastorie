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

  await page.goto("/?theme=light&speed=600");
  await expect(page.locator(".shelf .cover")).toHaveCount(4);

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

test("the parent corner exists but leads nowhere yet", async ({ page }) => {
  await page.goto("/?theme=light");
  await expect(page.locator(".parent-corner")).toBeVisible();
  await page.locator(".parent-corner").click();
  await expect(page.locator(".shelf")).toBeVisible(); // still on the shelf
});
