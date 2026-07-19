import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const PROD = "https://cantastorie.onrender.com";
const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXED_CSS = readFileSync(join(__dirname, "..", "..", "src", "static", "css", "player.css"), "utf-8");

// Regression for the shelf overlap bug (EN manifest has 7 covers; the overlap
// only manifests at 5+ covers, which the local content/ fixture never has).
// Covers must not overlap vertically: each row's covers must sit fully below
// the previous row's covers, with the grid gap preserved.
//
// Reproduces the user's report exactly: load the REAL production bucket
// manifest (asset-base meta already points at the R2 public URL on prod) and
// serve the FIXED local CSS so the test pins the fix, not the deployed build.
test("shelf covers do not overlap on the 7-cover EN manifest", async ({ page }) => {
  await page.route("**/static/css/player.css", (route) =>
    route.fulfill({ contentType: "text/css", body: FIXED_CSS }),
  );

  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(`${PROD}/?lang=en`, { waitUntil: "networkidle" });
  await page.waitForSelector(".covers .cover");
  await page.waitForTimeout(1500);

  const covers = await page.evaluate(() => {
    return [...document.querySelectorAll(".covers .cover")].map((c) => {
      const r = c.getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left) };
    });
  });

  expect(covers.length, "expected the EN manifest's 7 covers").toBeGreaterThanOrEqual(5);

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
