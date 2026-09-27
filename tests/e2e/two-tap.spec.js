// The two-tap acceptance test: cold load → tap the shelf (audio wakes,
// greeting plays) → tap a cover → the story begins. No cookies, and the
// only network traffic is the app's own pages and asset fetches.
//
// The default child language is English, whose fixture shelf has no prompt
// audio (so no greeting to wake to). The spec pins the Italian dev fixtures
// with ?lang=it: a five-cover shelf with a real greeting prompt, whose first
// cover is "La barchetta e la luna".

import { expect, test } from "@playwright/test";

// The Italian dev manifest (src/static/content/it/manifest.json) lists five.
const IT_SHELF_COVERS = 5;
const FONT_ORIGINS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"];

test("two taps from cold load to a story", async ({ page, context, baseURL }) => {
  const offOrigin = [];
  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== new URL(baseURL).origin && !FONT_ORIGINS.includes(origin)) {
      offOrigin.push(request.url());
    }
  });

  await page.goto("/play?lang=it&theme=light&speed=600");
  await expect(page.locator(".shelf .cover")).toHaveCount(IT_SHELF_COVERS);

  // The IT dev covers carry no cover art: the wash is the cover. Each must
  // still be a real, tappable box (#82 collapsed art-less covers to 0x0).
  for (const cover of await page.locator(".shelf .cover").all()) {
    const label = await cover.getAttribute("aria-label");
    await expect(cover.locator("img.cover-art"), `${label}: art-less fixture`).toHaveCount(0);
    const box = await cover.boundingBox();
    expect(box, `${label}: art-less cover has a layout box`).not.toBeNull();
    expect(box.width, `${label}: art-less cover collapsed to zero width`).toBeGreaterThan(0);
    expect(box.height, `${label}: art-less cover collapsed to zero height`).toBeGreaterThan(0);
  }

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

// product.md -> The Parent Area: "A small low-contrast corner of the shelf
// leads to everything grown-up." The corner once led nowhere; #35 made it a
// link and #82 points it at /parent, so the child-facing contract is that the corner is on the
// shelf and tapping it leaves the shelf for the parent area.
test("the parent corner leads from the shelf to the parent area", async ({ page }) => {
  await page.goto("/play?lang=it&theme=light");
  const corner = page.locator(".shelf .parent-corner");
  await expect(corner).toBeVisible();
  await expect(corner).toHaveAttribute("href", "/parent");
  await corner.click();
  await page.waitForURL((url) => url.pathname === "/parent");
  await expect(page.locator(".shelf")).toHaveCount(0); // left the child shelf
});
