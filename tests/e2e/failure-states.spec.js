// Failure-state acceptance (AI-367), named after docs/product.md ->
// "When Things Go Wrong": never dead air, never a spinner. Playwright's
// route interception plays the part of the truly bad night.

import { expect, test } from "@playwright/test";

const STORY_AUDIO = "**/stories/**/*.wav";
// The eight-page, hands-free Italian fixture: no branch overlay to pause
// the stall watchdog mid-test.
const STORY_TITLE = "La barchetta e la luna";

// Every AudioContext the player builds is kept on window.__contexts, so a
// test can play the part of iOS dropping the context (the tab backgrounds,
// a call comes in) with no test handle in the product.
function keepEveryAudioContext() {
  window.__contexts = [];
  for (const name of ["AudioContext", "webkitAudioContext"]) {
    const Native = window[name];
    if (typeof Native !== "function") continue;
    window[name] = class extends Native {
      constructor(...args) {
        super(...args);
        window.__contexts.push(this);
      }
    };
  }
}

// Given the Italian shelf: the first tap wakes the sound, the cover opens
// the story, and page 1's voice is speaking.
async function openTheStoryAndHearPageOne(page) {
  await page.addInitScript(keepEveryAudioContext);
  await page.goto("/play?lang=it&theme=dusk");
  await page.locator(".greeting").click();
  await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
  await page.getByRole("button", { name: STORY_TITLE }).click();
  await expect(page.locator(".player")).toBeVisible();
  await page.waitForFunction(() => window.__shell.engine.state === "playing");
  expect(await page.evaluate(() => window.__contexts.length)).toBe(1);
}

const contextState = (page) => page.evaluate(() => window.__contexts[0].state);

test.describe("When Things Go Wrong (product.md)", () => {
  test("audio won't load: the sleeping bird appears, speaks, and a tap wakes the story", async ({ page }) => {
    const retryPromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/audio-retry")) retryPromptRequests.push(request.url());
    });

    // Every narration file is dead before the night begins; the prompts live.
    await page.route(STORY_AUDIO, (route) => route.abort());

    // The Italian dev shelf: its fixture narration is .wav and it ships the
    // audio-retry prompt (the English fixtures are .mp3 with no prompts).
    await page.goto("/play?lang=it&theme=dusk");
    await page.locator(".greeting").click();
    await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
    await page.locator(".cover").first().click();

    // Then the bird holds the stage and its line is spoken — never silence.
    await expect(page.locator(".audio-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".audio-error .bird")).toBeVisible();
    await expect(page.locator(".audio-error .prompt")).toHaveText(
      "Oh! The story is taking a nap. Tap the bird to wake it up.",
    );
    expect(retryPromptRequests.length).toBeGreaterThan(0);

    // The network returns; tapping the bird wakes the story cleanly.
    await page.unroute(STORY_AUDIO);
    await page.locator(".audio-error").click();
    await expect(page.locator(".audio-error")).toHaveCount(0);
    await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "1", {
      timeout: 15_000,
    });
  });

  test("the shelf won't load: clouds speak, and when the sky clears a tap brings the stories", async ({ page }) => {
    const offlinePromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/offline")) offlinePromptRequests.push(request.url());
    });

    await page.route("**/manifest.json", (route) => route.abort());
    await page.goto("/play?theme=dusk");

    // Clouds, the line, no covers, no spinner.
    await expect(page.locator(".offline")).toBeVisible();
    await expect(page.locator(".offline .prompt")).toHaveText(
      "The clouds took the stories. Try again soon!",
    );
    await expect(page.locator(".cover")).toHaveCount(0);

    // A tap while still offline speaks and retries — the clouds remain.
    await page.locator(".offline").click();
    await expect(page.locator(".offline")).toBeVisible();
    expect(offlinePromptRequests.length).toBeGreaterThan(0);

    // The sky clears: the next tap loads the shelf.
    await page.unroute("**/manifest.json");
    await page.locator(".offline").click();
    await expect(page.locator(".cover").first()).toBeVisible({ timeout: 10_000 });
  });

  test("the voice freezes mid-story: the sleeping bird appears, and a tap wakes the story where it stopped", async ({ page }) => {
    await openTheStoryAndHearPageOne(page);

    // The context drops out from under a playing voice: no error, no
    // event, just a clock that stops. Silence is the one thing never allowed.
    await page.evaluate(() => window.__contexts[0].suspend());

    // Then the bird holds the stage within the stall watchdog's 2.5 s,
    // never dead air.
    await expect(page.locator(".audio-error")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator(".audio-error .bird")).toBeVisible();

    // Tapping the bird wakes the sound and the story, cleanly.
    await page.locator(".audio-error").click();
    await expect(page.locator(".audio-error")).toHaveCount(0);
    // resume() settles asynchronously, so the context's state is polled.
    await expect.poll(() => contextState(page), { timeout: 2_000 }).toBe("running");

    // And the voice is moving again. Each fixture page is a 1.2 s chime,
    // so over ~1 s the held voice may finish and turn the page, and
    // position() then restarts on the next page. So the assertion is "the
    // audio clock runs" plus "the story's spot is no longer frozen"
    // (position changed or the page turned), not a strictly larger position.
    const where = () =>
      page.evaluate(() => ({
        position: window.__shell.engine.position(),
        clock: window.__contexts[0].currentTime,
        page: document.querySelector(".page-wash.current")?.dataset.page ?? "end",
      }));
    const before = await where();
    await page.waitForTimeout(1_000);
    const after = await where();
    expect(after.clock - before.clock).toBeGreaterThan(0.5);
    expect(after.position !== before.position || after.page !== before.page).toBe(true);
    await expect(page.locator(".audio-error")).toHaveCount(0);
  });

  test("the sound drops while away: coming back and tapping the story wakes it again", async ({ page }) => {
    await openTheStoryAndHearPageOne(page);

    // The context is dropped, then the page returns to view. Headless
    // Chromium never hides the tab, so visibility is redefined to report
    // "visible" before the event fires.
    await page.evaluate(() => {
      window.__contexts[0].suspend();
      Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true });
      Object.defineProperty(document, "hidden", { get: () => false, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Then a tap on the story wakes the sound. It is a raw tap on the page
    // art (no button there, so the story keeps playing), not a locator
    // click: the suspend-to-tap window is one round trip, far inside the
    // watchdog's 2.5 s, but if the bird ever appeared first on a slow
    // machine, the same tap would land on it and still resume the context.
    // "running" is the assertion either way, so the test cannot flake on
    // which of the two the child sees.
    await page.mouse.click(200, 300);
    await expect.poll(() => contextState(page), { timeout: 2_000 }).toBe("running");
  });
});
