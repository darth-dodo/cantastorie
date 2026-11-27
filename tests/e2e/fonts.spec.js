// Self-hosted fonts (AI-505, #164): Baloo 2 and Literata render from
// /static/fonts/, not from a fallback, and nothing is fetched from Google.
// The rendered font comes from Chrome DevTools (CSS.getPlatformFontsForNode),
// which names the font face that actually drew the glyphs.

import { expect, test } from "@playwright/test";

async function renderedFonts(page, selector) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const { root } = await cdp.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
  await cdp.detach();
  return fonts;
}

async function fontFiles(page) {
  return page.evaluate(() =>
    performance
      .getEntriesByType("resource")
      .map((entry) => new URL(entry.name).pathname)
      .filter((path) => path.endsWith(".woff2")),
  );
}

async function expectDrawnBy(page, selector, family) {
  await page.evaluate(() => document.fonts.ready);
  const fonts = await renderedFonts(page, selector);
  expect(fonts.length, `${selector} rendered no glyphs`).toBeGreaterThan(0);
  for (const font of fonts) {
    expect(font.familyName, `${selector} fell back to ${font.familyName}`).toBe(family);
    expect(font.isCustomFont).toBe(true);
  }
}

test("the shelf greeting is drawn in Baloo 2 from /static/fonts/", async ({ page }) => {
  await page.goto("/play?lang=it&theme=light");
  await expect(page.locator(".greeting h1")).not.toBeEmpty();
  await expectDrawnBy(page, ".greeting h1", "Baloo 2");
  expect(await fontFiles(page)).toContain("/static/fonts/baloo-2/baloo-2-latin.woff2");
});

test("a Hindi shelf greeting is drawn in Baloo 2's Devanagari subset", async ({ page }) => {
  await page.goto("/play?lang=hi&theme=light");
  await expect(page.locator(".greeting h1")).not.toBeEmpty();
  await expectDrawnBy(page, ".greeting h1", "Baloo 2");
  expect(await fontFiles(page)).toContain("/static/fonts/baloo-2/baloo-2-devanagari.woff2");
});

test("story text is drawn in Literata from /static/fonts/", async ({ page }) => {
  // The player has no Literata text yet (reading mode is not built); the 404
  // heading and the landing tagline are the public pages that set it.
  await page.goto("/no-such-page?theme=light");
  await expectDrawnBy(page, ".not-found__heading", "Literata");
  await page.goto("/?theme=light");
  await page.locator(".l-footer-tagline").scrollIntoViewIfNeeded();
  await expectDrawnBy(page, ".l-footer-tagline", "Literata");
  expect(await fontFiles(page)).toContain("/static/fonts/literata/literata-latin.woff2");
});

test("no page fetches anything from Google Fonts", async ({ page }) => {
  const google = [];
  page.on("request", (request) => {
    if (/fonts\.(googleapis|gstatic)\.com/.test(request.url())) google.push(request.url());
  });
  for (const path of ["/", "/play?lang=it", "/no-such-page"]) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
  }
  expect(google).toEqual([]);
});
