import { test, expect } from "@playwright/test";
import { openSevenCoverShelf, SEVEN_COVER_MANIFEST } from "./seven-cover-shelf.js";

// Regression for #204: the tilted cover's 7px sticker ring painted over the top
// of the caption's first line, where the Devanagari vowel loops (ि, ी), the
// e-matra and the anusvara sit, so "अंतराळातील पिकनिक" read as "अंतराळाताल ापकानक".
// A box-shadow is not hit-testable, so document.elementFromPoint cannot see the
// ring. Each caption is instead compared, pixel for pixel, against a reference
// render of the same shelf with every caption forced above every cover.

async function forceCaptionsOnTop(page, on) {
  await page.evaluate((lift) => {
    for (const el of document.querySelectorAll(".covers .cover-caption")) {
      el.style.position = lift ? "relative" : "";
      el.style.zIndex = lift ? "2147483647" : "";
    }
  }, on);
}

const SHELVES = {
  mr: [
    "अंतराळातील पिकनिक",
    "पावसाचे थेंब आणि इंद्रधनुष्य",
    "चिमणीची गोष्ट",
    "आजीच्या गावाला",
    "किल्ल्यावरची सहल",
    "निळी नदी",
    "पिकलेली कैरी",
  ],
  en: [
    "Summer Vacation with Grandma and Grandpa",
    "Pip the Pirate",
    "The Very Sleepy Bear Who Could Not Find His Blanket",
    "Cosmo",
    "A Lighthouse at the Edge of the World",
    "Luna and the Lantern",
    "Seven Little Boats Sailing Home Under the Moon",
  ],
};

for (const [lang, titles] of Object.entries(SHELVES)) {
  for (const width of [320, 360, 390, 768]) {
    test(`${lang} captions paint above the covers at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 874 });
      await openSevenCoverShelf(page, {
        ...SEVEN_COVER_MANIFEST,
        language: lang,
        stories: SEVEN_COVER_MANIFEST.stories.map((s, i) => ({ ...s, title: titles[i] })),
      });

      const captions = page.locator(".covers .cover-caption");
      await expect(captions).toHaveCount(titles.length);
      const overpainted = [];
      for (let i = 0; i < titles.length; i++) {
        const caption = captions.nth(i);
        await caption.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
        const clip = await caption.boundingBox();
        const shelf = await page.screenshot({ clip, animations: "disabled" });
        await forceCaptionsOnTop(page, true);
        const unclipped = await page.screenshot({ clip, animations: "disabled" });
        await forceCaptionsOnTop(page, false);
        if (!shelf.equals(unclipped)) overpainted.push(titles[i]);
      }
      expect(overpainted, "captions painted over by a cover").toEqual([]);
    });
  }
}
