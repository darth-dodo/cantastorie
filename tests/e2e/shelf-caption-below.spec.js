import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const PROD = "https://cantastorie.onrender.com";
const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXED_CSS = readFileSync(join(__dirname, "..", "..", "src", "static", "css", "player.css"), "utf-8");

// The story title must sit in a block BELOW the cover image, not as an overlay
// on top of it. Loads the REAL production bucket manifest (asset-base meta
// already points at the R2 public URL) with the FIXED local CSS injected so
// the test pins the fix, not the deployed build.
test("story title renders below the cover image, not as an overlay", async ({ page }) => {
  await page.route("**/static/css/player.css", (route) =>
    route.fulfill({ contentType: "text/css", body: FIXED_CSS }),
  );

  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(`${PROD}/?lang=en`, { waitUntil: "networkidle" });
  await page.waitForSelector(".covers .cover");
  await page.waitForTimeout(1500);

  // offsetTop/offsetHeight are layout-space and ignore the wobble transform:
  // getBoundingClientRect would skew the rotated box and falsely show overlap.
  const covers = await page.evaluate(() => {
    return [...document.querySelectorAll(".covers .cover")].map((c) => {
      const img = c.querySelector("img.cover-art");
      const span = c.querySelector("span");
      return {
        imgBottom: img.offsetTop + img.offsetHeight,
        spanTop: span.offsetTop,
        spanText: span.textContent.trim(),
        ariaLabel: c.getAttribute("aria-label"),
      };
    });
  });

  expect(covers.length, "expected the EN manifest's 7 covers").toBeGreaterThanOrEqual(5);

  for (const [i, c] of covers.entries()) {
    expect(c.spanTop, `cover ${i}: caption (${c.spanTop}) overlaps or sits above the image bottom (${c.imgBottom})`).toBeGreaterThanOrEqual(c.imgBottom - 1);
    // Title must be present and match the story title (aria-label).
    expect(c.spanText, `cover ${i}: caption text is empty`).not.toBe("");
    expect(c.spanText, `cover ${i}: caption should equal the story title`).toBe(c.ariaLabel);
  }

  // Regression: #59 overlap fix must hold — no two covers in a column overlap.
  // Layout-space offsets ignore the wobble transform, unlike getBoundingClientRect.
  const boxes = await page.evaluate(() =>
    [...document.querySelectorAll(".covers .cover")].map((c) => ({
      top: c.offsetTop,
      bottom: c.offsetTop + c.offsetHeight,
      left: c.offsetLeft,
    })),
  );
  const lefts = [...new Set(boxes.map((b) => b.left))].sort((a, b) => a - b);
  for (const l of lefts) {
    const col = boxes.filter((b) => b.left === l).sort((a, b) => a.top - b.top);
    for (let i = 1; i < col.length; i++) {
      const gap = col[i].top - col[i - 1].bottom;
      expect(gap, `covers overlap by ${-gap}px (regression of #59)`).toBeGreaterThanOrEqual(0);
    }
  }

  expect(errors, `page errors: ${errors.join("; ")}`).toHaveLength(0);
});
