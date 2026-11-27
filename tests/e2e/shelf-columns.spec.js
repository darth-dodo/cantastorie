import { test, expect } from "@playwright/test";
import { openSevenCoverShelf, SEVEN_COVER_MANIFEST } from "./seven-cover-shelf.js";

// Regression for #180: a `1fr 1fr` grid track cannot shrink below its content's
// minimum width, so cover art at its natural width pushed each column wider
// than half the shelf and `.covers` clipped the right column. At every phone
// and tablet width the shelf must show two whole covers per row, every title
// readable, the four wobble shapes cycling, and the child's 96px tap target.

const LONG_TITLES = [
  "Summer Vacation with Grandma and Grandpa",
  "Pip the Pirate",
  "The Very Sleepy Bear Who Could Not Find His Blanket",
  "Cosmo",
  "A Lighthouse at the Edge of the World",
  "Luna and the Lantern",
  "Seven Little Boats Sailing Home Under the Moon",
];

const LONG_TITLE_MANIFEST = {
  ...SEVEN_COVER_MANIFEST,
  stories: SEVEN_COVER_MANIFEST.stories.map((s, i) => ({ ...s, title: LONG_TITLES[i] })),
};

const TAP_TARGET = 96;

for (const width of [320, 360, 390, 414, 768]) {
  test(`shelf shows two whole covers per row at ${width}px`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 874 });
    await openSevenCoverShelf(page, LONG_TITLE_MANIFEST);

    const layout = await page.evaluate(() => {
      const covers = document.querySelector(".covers");
      const shelf = covers.getBoundingClientRect();
      const cards = [...covers.querySelectorAll(".cover-card")].map((card) => {
        const r = card.getBoundingClientRect();
        const cover = card.querySelector(".cover");
        const caption = card.querySelector(".cover-caption");
        const lineHeight = parseFloat(getComputedStyle(caption).lineHeight);
        return {
          left: r.left,
          right: r.right,
          width: card.offsetWidth,
          top: Math.round(card.offsetTop),
          coverWidth: cover.offsetWidth,
          coverHeight: cover.offsetHeight,
          captionScroll: caption.scrollWidth,
          captionClient: caption.clientWidth,
          captionWraps: caption.offsetHeight > lineHeight * 1.5,
          shape: getComputedStyle(cover).borderTopLeftRadius,
        };
      });
      return {
        shelfLeft: shelf.left,
        shelfRight: shelf.right,
        cards,
      };
    });

    expect(layout.cards).toHaveLength(LONG_TITLES.length);
    for (const [i, c] of layout.cards.entries()) {
      expect(c.left, `card ${i} starts left of the shelf`).toBeGreaterThanOrEqual(
        layout.shelfLeft - 0.5,
      );
      expect(c.right, `card ${i} extends past the shelf`).toBeLessThanOrEqual(
        layout.shelfRight + 0.5,
      );
      expect(c.coverWidth, `card ${i} cover narrower than the tap target`).toBeGreaterThanOrEqual(
        TAP_TARGET,
      );
      expect(c.coverHeight, `card ${i} cover shorter than the tap target`).toBeGreaterThanOrEqual(
        TAP_TARGET,
      );
      expect(
        c.captionScroll,
        `card ${i} caption "${LONG_TITLES[i]}" is clipped (${c.captionScroll} > ${c.captionClient})`,
      ).toBeLessThanOrEqual(c.captionClient);
    }

    const widths = layout.cards.map((c) => c.width);
    expect(
      Math.max(...widths) - Math.min(...widths),
      `the two columns share the shelf equally: ${widths}`,
    ).toBeLessThanOrEqual(1);

    const rows = new Map();
    for (const c of layout.cards) rows.set(c.top, (rows.get(c.top) ?? 0) + 1);
    const perRow = [...rows.values()];
    expect(perRow.slice(0, -1).every((n) => n === 2), `cards per row: ${perRow}`).toBe(true);
    expect(perRow.at(-1), `last row: ${perRow}`).toBe(LONG_TITLES.length % 2 || 2);

    const shapes = layout.cards.map((c) => c.shape);
    expect(new Set(shapes.slice(0, 4)).size, `first four shapes: ${shapes}`).toBe(4);
    for (let i = 4; i < shapes.length; i++) {
      expect(shapes[i], `card ${i} repeats shape ${i % 4}`).toBe(shapes[i % 4]);
    }

    expect(errors, `page errors: ${errors.join("; ")}`).toHaveLength(0);
  });

  test(`language button clears the last row's caption at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 874 });
    await openSevenCoverShelf(page, LONG_TITLE_MANIFEST);
    await page.evaluate(() => {
      const covers = document.querySelector(".covers");
      covers.scrollTop = covers.scrollHeight;
    });
    const overlaps = await page.evaluate(() => {
      const sticker = document.querySelector(".lang-sticker").getBoundingClientRect();
      return [...document.querySelectorAll(".covers .cover-caption")]
        .map((el) => ({ text: el.textContent, r: el.getBoundingClientRect() }))
        .filter(
          ({ r }) =>
            r.left < sticker.right &&
            r.right > sticker.left &&
            r.top < sticker.bottom &&
            r.bottom > sticker.top,
        )
        .map(({ text }) => text);
    });
    expect(overlaps, "captions under the language button").toEqual([]);
  });
}
