import { test, expect } from "@playwright/test";
import { openSevenCoverShelf, SEVEN_COVER_MANIFEST } from "./seven-cover-shelf.js";

// The story title must sit in a block BELOW the cover image, not as an overlay
// on top of it (#60). Since #82 the caption is a sibling of the cover button
// inside a .cover-card, so "below" means: the caption lives outside the cover
// and starts at or under the cover's bottom edge. Runs on a stubbed 7-cover EN
// shelf with real cover art (see seven-cover-shelf.js), against this
// checkout's own JS and CSS.
test("story title renders below the cover image, not as an overlay", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await openSevenCoverShelf(page);

  // offsetTop/offsetHeight are layout-space and ignore the wobble transform:
  // getBoundingClientRect would skew the rotated box and falsely show overlap.
  const cards = await page.evaluate(() =>
    [...document.querySelectorAll(".covers .cover-card")].map((card) => {
      const cover = card.querySelector(".cover");
      const img = cover.querySelector("img.cover-art");
      const caption = card.querySelector(".cover-caption");
      return {
        hasArt: !!img,
        artFillsCover: !!img && img.offsetHeight > 0 && img.offsetHeight <= cover.offsetHeight,
        captionInsideCover: !!caption && cover.contains(caption),
        coverBottom: cover.offsetTop + cover.offsetHeight,
        captionTop: caption ? caption.offsetTop : null,
        captionText: caption ? caption.textContent.trim() : "",
        ariaLabel: cover.getAttribute("aria-label"),
      };
    }),
  );

  expect(cards.length, "expected the stubbed EN manifest's 7 covers").toBe(
    SEVEN_COVER_MANIFEST.stories.length,
  );

  for (const [i, c] of cards.entries()) {
    expect(c.hasArt, `cover ${i}: cover art image rendered`).toBe(true);
    expect(c.artFillsCover, `cover ${i}: cover art sits inside the cover`).toBe(true);
    expect(c.captionInsideCover, `cover ${i}: caption must not overlay the cover`).toBe(false);
    expect(
      c.captionTop,
      `cover ${i}: caption (${c.captionTop}) overlaps or sits above the cover bottom (${c.coverBottom})`,
    ).toBeGreaterThanOrEqual(c.coverBottom - 1);
    // Title must be present and match the story title (aria-label).
    expect(c.captionText, `cover ${i}: caption text is empty`).not.toBe("");
    expect(c.captionText, `cover ${i}: caption should equal the story title`).toBe(c.ariaLabel);
  }

  // Regression: #59 overlap fix must hold — no two cards in a column overlap.
  // Layout-space offsets ignore the wobble transform, unlike getBoundingClientRect.
  const boxes = await page.evaluate(() =>
    [...document.querySelectorAll(".covers .cover-card")].map((c) => ({
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
