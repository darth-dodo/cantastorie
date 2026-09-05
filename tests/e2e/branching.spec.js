// Branching acceptance (AI-428): the child-visible proof that a tapped
// choice really follows its arm to that arm's own ending. Named after
// docs/product.md -> "Picture choices" and the dev fixture "Il bivio della
// luna" (src/static/content/it/stories/dev-branching): shared pages p1..p6,
// a choice on p6, and two 4-page arms (a1..a4 / b1..b4).
//
// The default child language is English now (main's i18n change), and the
// branching fixture only lives in the Italian shelf, so these open with
// ?lang=it. Each fixture page's narration is a seconds-long chime, so the
// whole night — shared prefix, choice, chosen arm, end screen — runs
// hands-free in well under a minute; the only tap is the choice itself.

import { expect, test } from "@playwright/test";

// The dev-branching cover is the fifth (last) entry in the it manifest.
const BRANCHING_COVER_INDEX = 4;

// The story.json the fixture cover loads — intercepted below to inject cards.
const STORY_URL = "**/content/it/stories/dev-branching/story.json";

// The choice prompt and the two real option labels from the fixture.
const CHOICE_PROMPT = "quale strada prende Nina?";
const ARM_A_LABEL = "il sentiero d'argento della luna";
const ARM_B_LABEL = "la baia buia dei gabbiani";

// A page unique to each arm: the first arm page's full-bleed art. Arm A opens
// on a1's image (p7.*), arm B on b1's (p3.*) — distinct files, so the page-art
// background tells the arms apart. p3 also appears in the shared prefix, but
// p7 never does, so arm A's art is the reliable "not this arm" marker.
const ARM_A_FIRST_ART = "p7.c90cff65.webp";
const ARM_B_FIRST_ART = "p3.c90cff65.webp";

// The played path after a branch: 6 shared pages + a 4-page arm; the arm's
// first page lands at index 6.
const ARM_FIRST_PAGE_INDEX = 6;
const BRANCHED_PATH_LENGTH = 10;

async function wakeAndOpenBranchingStory(page) {
  await page.goto("/play?lang=it&theme=light");
  // The first tap anywhere wakes the sound...
  await page.locator(".greeting").click();
  await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
  // ...and the fifth cover opens the branching fixture.
  await page.locator(".cover").nth(BRANCHING_COVER_INDEX).click();
  await expect(page.locator(".player")).toBeVisible();
}

// The story-arts on screen, in page order, by filename. Persists from the
// choice-close rebuild until the end screen replaces the player.
function armArtByIndex(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll(".page-art")].map((art) => {
      const match = art.style.backgroundImage.match(/\/([^/"]+\.[a-z]+)"?\)?$/i);
      return match ? match[1] : null;
    }),
  );
}

test("a tapped choice leads to that arm's ending", async ({ page }) => {
  await wakeAndOpenBranchingStory(page);

  // Narration drives the shared prefix hands-free; on the choice page's audio
  // end the overlay opens with the prompt and both option buttons.
  await expect(page.locator(".overlay .prompt")).toHaveText(CHOICE_PROMPT, { timeout: 20_000 });
  const options = page.locator(".overlay .option");
  await expect(options).toHaveCount(2);
  await expect(options.nth(0)).toHaveAttribute("aria-label", ARM_A_LABEL);
  await expect(options.nth(1)).toHaveAttribute("aria-label", ARM_B_LABEL);

  // Tap option 2 (arm B): the played path extends with the chosen arm and the
  // story turns onto b1 — a page the shared prefix never reaches.
  await options.nth(1).click();
  await expect(page.locator(".overlay")).toHaveCount(0);

  // The extended path is now 10 pages, and arm B's own first art (p3) sits at
  // the arm's start index — the arm-unique page really rendered.
  await page.waitForFunction(
    ({ index, art }) => {
      const node = document.querySelector(`.page-art[data-page="${index}"]`);
      return !!node && node.style.backgroundImage.includes(art);
    },
    { index: ARM_FIRST_PAGE_INDEX, art: ARM_B_FIRST_ART },
    { timeout: 20_000 },
  );
  await expect(page.locator(".bead")).toHaveCount(BRANCHED_PATH_LENGTH);
  const arts = await armArtByIndex(page);
  expect(arts[ARM_FIRST_PAGE_INDEX]).toBe(ARM_B_FIRST_ART);
  expect(arts).not.toContain(ARM_A_FIRST_ART); // arm A's pages were never appended

  // With no further taps, the arm plays to its end screen.
  await expect(page.locator(".end")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Again!" })).toBeVisible();
});

// Layout regression guard for commit 699cb9e (AI-428). Task 11 added
// <img class="choice-card-image"> to the overlay but no CSS sized it, so a
// real published card (768px natural) rendered unconstrained inside the fixed
// 164x196 .choice-card and blew the overlay out to one full-bleed image. The
// dev fixture's options are card_image:null (wash fallback), which never
// exercises the img path — so this test injects a real, deliberately-large
// (800px) same-origin card into the story.json response, then asserts each
// rendered card image is clamped to its card box and never overflows the
// viewport. jsdom can't make this assertion; it needs a real layout engine.
test("choice card images are clamped to their card box (layout regression)", async ({ page }) => {
  // Rewrite the fixture's two options to carry a real card. story.js resolves
  // card_image as `base + card_image`, where base is the story dir
  // /static/content/it/stories/dev-branching/ — so four "../" climb back to
  // /static/ and reach the bundled 800x800 PNG under /static/img/. Same-origin,
  // so this never depends on R2/CORS/network.
  const CARD = "../../../../img/choice-card-test.png";
  await page.route(STORY_URL, async (route) => {
    const response = await route.fetch();
    const story = await response.json();
    const choicePage = story.pages.find((p) => p.choice);
    for (const option of choicePage.choice.options) option.card_image = CARD;
    await route.fulfill({ json: story });
  });

  await wakeAndOpenBranchingStory(page);

  // The overlay opens with both cards rendering the injected image.
  await expect(page.locator(".overlay .prompt")).toHaveText(CHOICE_PROMPT, { timeout: 20_000 });
  const images = page.locator(".overlay .choice-card-image");
  await expect(images).toHaveCount(2);
  // The <img> really loaded the injected card (not a broken/zero-size image).
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll(".overlay .choice-card-image")].every(
        (img) => img.complete && img.naturalWidth > 400,
      ),
    { timeout: 20_000 },
  );

  const viewport = page.viewportSize();
  const count = await images.count();
  for (let i = 0; i < count; i++) {
    const image = images.nth(i);
    const card = image.locator("xpath=ancestor::div[contains(@class,'choice-card')][1]");
    const imageBox = await image.boundingBox();
    const cardBox = await card.boundingBox();
    expect(imageBox, "card image has a layout box").not.toBeNull();
    expect(cardBox, "card has a layout box").not.toBeNull();

    // The 800px image is clamped to the card box (object-fit: cover on the img,
    // overflow: hidden on the card) — not rendered at its natural size.
    expect(imageBox.width).toBeLessThanOrEqual(cardBox.width + 1);
    expect(imageBox.height).toBeLessThanOrEqual(cardBox.height + 1);
    // And the card itself never overflows the phone viewport.
    expect(cardBox.width).toBeLessThanOrEqual(viewport.width);
  }
});

// Auto-continue (nudge at 30s, auto-pick the first option at 40s) is specified
// for the choice overlay but not yet wired in playback.js — the loop simply
// waits on an open choice (see the file header there: "the overlay, nudge, and
// auto-continue are AI-370"). Skipped as follow-up scope until those timers
// land; noted in the AI-428 report. When wired, this proves a hands-off child
// still reaches arm A's own ending.
test.skip("auto-continue picks the first option", async ({ page }) => {
  await wakeAndOpenBranchingStory(page);

  await expect(page.locator(".overlay .option")).toHaveCount(2, { timeout: 20_000 });

  // Wait out the nudge + auto-continue window with no tap; the first option
  // (arm A) should be chosen for the child.
  await page.waitForFunction(
    ({ index, art }) => {
      const node = document.querySelector(`.page-art[data-page="${index}"]`);
      return !!node && node.style.backgroundImage.includes(art);
    },
    { index: ARM_FIRST_PAGE_INDEX, art: ARM_A_FIRST_ART },
    { timeout: 60_000 },
  );
  const arts = await armArtByIndex(page);
  expect(arts[ARM_FIRST_PAGE_INDEX]).toBe(ARM_A_FIRST_ART);
});
