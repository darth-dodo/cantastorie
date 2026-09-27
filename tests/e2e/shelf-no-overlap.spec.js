import { test, expect } from "@playwright/test";
import { openSevenCoverShelf, SEVEN_COVER_MANIFEST } from "./seven-cover-shelf.js";

// Regression for the shelf overlap bug (#59): the EN manifest had 7 covers and
// the overlap only manifests at 5+ covers, which the local EN fixture (3
// stories) never reaches. Covers must not overlap vertically: each row's
// covers must sit fully below the previous row's covers, with the grid gap
// preserved. Runs on a stubbed 7-cover EN shelf with real cover art (see
// seven-cover-shelf.js), against this checkout's own JS and CSS.
test("shelf covers do not overlap on the 7-cover EN manifest", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await openSevenCoverShelf(page);

  const covers = await page.evaluate(() => {
    return [...document.querySelectorAll(".covers .cover")].map((c) => {
      const r = c.getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left) };
    });
  });

  expect(covers.length, "expected the stubbed EN manifest's 7 covers").toBe(
    SEVEN_COVER_MANIFEST.stories.length,
  );

  // Cluster covers into columns by their left edge (viewport-agnostic: side-by-
  // side covers share a left; the two columns are the distinct left values).
  const lefts = [...new Set(covers.map((c) => c.left))].sort((a, b) => a - b);
  const columns = lefts.map((l) => covers.filter((c) => c.left === l).sort((a, b) => a.top - b.top));

  for (const col of columns) {
    for (let i = 1; i < col.length; i++) {
      const gap = col[i].top - col[i - 1].bottom;
      expect(gap, `covers in a column overlap by ${-gap}px at row ${i}`).toBeGreaterThanOrEqual(0);
    }
  }

  expect(errors, `page errors: ${errors.join("; ")}`).toHaveLength(0);
});
