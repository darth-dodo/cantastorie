// Keyboard and narrow-screen accessibility for the child player (AI-498):
// a visible focus ring for keyboard users only (M20), AA cover captions in
// the light theme (M26), and the page chevrons stay on screen at the
// narrowest supported phone width (M27).

import { expect, test } from "@playwright/test";

const outlineOf = (locator) =>
  locator.evaluate((node) => {
    const cs = getComputedStyle(node);
    return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth) };
  });

for (const theme of ["light", "dusk"]) {
  test(`keyboard Tab shows a focus ring; a click does not (${theme})`, async ({ page }) => {
    await page.goto(`/play?lang=it&theme=${theme}`);
    await expect(page.locator(".shelf .cover").first()).toBeVisible();

    // A mouse click focuses the gear but must not paint the ring.
    await page.locator(".settings-gear").click();
    await expect(page.locator(".settings-sheet")).toBeVisible();
    await page.locator(".settings-close-pill").click();
    await expect(page.locator(".settings-sheet")).toHaveCount(0);
    await page.mouse.click(5, 5);

    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toHaveCount(1);
    const ring = await outlineOf(focused);
    expect(ring.style).toBe("solid");
    expect(ring.width).toBeGreaterThanOrEqual(2);
  });
}

test("at 320px the page chevrons stay on screen and clear of play-pause", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto("/play?lang=it&theme=light&speed=600");
  await page.locator(".cover").first().click();
  await expect(page.locator(".player")).toBeVisible();
  // Page 1 so the previous chevron is shown too.
  await page.locator(".nav-next").click();
  await expect(page.locator(".nav-prev")).toBeVisible();

  const play = await page.locator(".play-pause").boundingBox();
  for (const sel of [".nav-prev", ".nav-next"]) {
    const box = await page.locator(sel).boundingBox();
    expect(box.x, `${sel} left edge`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${sel} right edge`).toBeLessThanOrEqual(320);
    const overlaps = box.x < play.x + play.width && play.x < box.x + box.width;
    expect(overlaps, `${sel} overlaps play-pause`).toBe(false);
  }
});

test("light-theme cover captions use the AA caption ink (M26)", async ({ page }) => {
  await page.goto("/play?lang=it&theme=light");
  const caption = page.locator(".cover-caption").first();
  await expect(caption).toBeVisible();
  // #5F687B on --surface #F2F4F8 is 5.1:1; the old --ink-soft was 3.4:1.
  expect(await caption.evaluate((node) => getComputedStyle(node).color)).toBe("rgb(95, 104, 123)");
});
