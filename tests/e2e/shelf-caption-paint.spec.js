import { test, expect } from "@playwright/test";
import {
  openSevenCoverShelf,
  SEVEN_COVER_MANIFEST,
} from "./seven-cover-shelf.js";

// Regression for #204: the tilted cover's 7px sticker ring painted over the top
// of the caption's first line, where the Devanagari vowel loops (ि, ी), the
// e-matra and the anusvara sit, so "अंतराळातील पिकनिक" read as "अंतराळाताल ापकानक".
// A box-shadow is not hit-testable, so document.elementFromPoint cannot see the
// ring. Each caption is instead compared, pixel for pixel, against a reference
// render of the same shelf where the covers form their own stacking context and
// each caption sits just above its own cover inside it. The reference keeps the
// shelf's bottom fade and the shelf buttons above the captions, so a caption
// lifted past the fade fails too.

async function renderReference(page, on) {
  await page.evaluate((ref) => {
    document.querySelector(".covers").style.isolation = ref ? "isolate" : "";
    for (const el of document.querySelectorAll(".covers .cover-caption")) {
      el.style.position = ref ? "relative" : "";
      el.style.zIndex = ref ? "1" : "";
    }
  }, on);
}

async function matchesReference(page, clip) {
  const shelf = await page.screenshot({ clip, animations: "disabled" });
  await renderReference(page, true);
  const reference = await page.screenshot({ clip, animations: "disabled" });
  await renderReference(page, false);
  return shelf.equals(reference);
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
  hi: [
    "अंतरिक्ष में पिकनिक",
    "बारिश की बूँदें और इंद्रधनुष",
    "चिड़िया की कहानी",
    "दादी के गाँव में",
    "किले की सैर",
    "नीली नदी",
    "पका हुआ आम",
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

function shelfOf(lang, titles) {
  return {
    ...SEVEN_COVER_MANIFEST,
    language: lang,
    stories: SEVEN_COVER_MANIFEST.stories.map((s, i) => ({
      ...s,
      title: titles[i],
    })),
  };
}

for (const [lang, titles] of Object.entries(SHELVES)) {
  for (const width of [320, 360, 390, 768]) {
    test(`${lang} captions paint above the covers at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 874 });
      await openSevenCoverShelf(page, shelfOf(lang, titles));

      const captions = page.locator(".covers .cover-caption");
      await expect(captions).toHaveCount(titles.length);
      const overpainted = [];
      for (let i = 0; i < titles.length; i++) {
        const caption = captions.nth(i);
        await caption.evaluate((el) =>
          el.scrollIntoView({ block: "center", behavior: "instant" }),
        );
        if (!(await matchesReference(page, await caption.boundingBox()))) {
          overpainted.push(titles[i]);
        }
      }
      expect(overpainted, "captions painted over by a cover").toEqual([]);
    });

    test(`${lang} captions fade with their covers at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 874 });
      await openSevenCoverShelf(page, shelfOf(lang, titles));

      // Scroll a caption into the part of the shelf's 120px bottom fade that the
      // covers still show: the first caption whose scroll offset can reach it.
      const index = await page.evaluate(() => {
        const shelf = document.querySelector(".shelf").getBoundingClientRect();
        const covers = document.querySelector(".covers");
        const box = covers.getBoundingClientRect();
        const fadeTop = Math.max(shelf.bottom - 120, box.top);
        const target = (fadeTop + Math.min(box.bottom, shelf.bottom)) / 2;
        const maxScroll = covers.scrollHeight - covers.clientHeight;
        covers.scrollTop = 0;
        const all = [...covers.querySelectorAll(".cover-caption")];
        for (const [i, caption] of all.entries()) {
          const r = caption.getBoundingClientRect();
          const scroll = (r.top + r.bottom) / 2 - target;
          if (scroll < 0 || scroll > maxScroll) continue;
          covers.scrollTop = scroll;
          const after = caption.getBoundingClientRect();
          if (after.top >= fadeTop && after.bottom <= box.bottom) return i;
          covers.scrollTop = 0;
        }
        return -1;
      });
      expect(
        index,
        "a caption sits inside the bottom fade",
      ).toBeGreaterThanOrEqual(0);

      const clip = await page
        .locator(".covers .cover-caption")
        .nth(index)
        .boundingBox();
      expect(
        await matchesReference(page, clip),
        `"${titles[index]}" fades with its cover`,
      ).toBe(true);
    });
  }
}
