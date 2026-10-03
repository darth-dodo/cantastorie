// The two-tap acceptance test: cold load → tap the shelf (audio wakes,
// greeting plays) → tap a cover → the story begins. No cookies, and the
// only network traffic is the app's own pages and asset fetches.
//
// The default child language is English. The spec pins the Italian dev
// fixtures with ?lang=it instead, so it never depends on which languages have
// prompt fixtures committed: a five-cover shelf that always ships a greeting
// prompt, whose first cover is "La barchetta e la luna".

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

// product.md -> The Parent Area: the way to everything grown-up starts on the
// shelf. The design overhaul (AI-444) replaced the low-contrast parent corner
// with the settings sheet's "The workshop" row behind the grown-up gate (a
// sum a child can't answer), so a stray tap can no longer leave the shelf.
test("the shelf leads to the parent area only through the grown-up gate", async ({ page }) => {
  await page.goto("/play?lang=it&theme=light");
  await expect(page.locator(".shelf .parent-corner")).toHaveCount(0); // no open door

  await page.locator(".settings-gear").click();
  await page.locator("button.settings-row--workshop").click();
  await expect(page.locator(".gate-modal")).toBeVisible();

  // 7 + 6: the right answer opens the door.
  await page.locator(".gate-choice", { hasText: "13" }).click();
  await page.waitForURL((url) => url.pathname === "/parent");
  await expect(page.locator(".shelf")).toHaveCount(0); // left the child shelf
});
