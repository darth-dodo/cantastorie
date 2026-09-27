// The favicon (AI-467): every icon a page links must actually decode in a real
// browser. An SVG with "--" inside an XML comment serves fine with a 200 and
// the right content type, yet the browser drops it silently. Only decoding it
// catches that.

import { expect, test } from "@playwright/test";

for (const path of ["/", "/play"]) {
  test(`every icon linked from ${path} decodes`, async ({ page }) => {
    await page.goto(path);
    const hrefs = await page
      .locator('link[rel="icon"], link[rel="apple-touch-icon"]')
      .evaluateAll((links) => links.map((l) => l.href));
    expect(hrefs.length).toBe(3);

    for (const href of hrefs) {
      const width = await page.evaluate(async (src) => {
        const img = new Image();
        img.src = src;
        await img.decode();
        return img.naturalWidth;
      }, href);
      expect(width, `${href} did not decode`).toBeGreaterThan(0);
    }
  });
}
