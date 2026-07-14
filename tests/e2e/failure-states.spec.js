// Failure-state acceptance (AI-367), named after docs/product.md ->
// "When Things Go Wrong": never dead air, never a spinner. Playwright's
// route interception plays the part of the truly bad night.

import { expect, test } from "@playwright/test";

const STORY_AUDIO = "**/stories/**/*.wav";

test.describe("When Things Go Wrong (product.md)", () => {
  test("audio won't load: the sleeping bird appears, speaks, and a tap wakes the story", async ({ page }) => {
    const retryPromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/audio-retry")) retryPromptRequests.push(request.url());
    });

    // Every narration file is dead before the night begins; the prompts live.
    await page.route(STORY_AUDIO, (route) => route.abort());

    await page.goto("/?theme=dusk");
    await page.locator(".greeting").click();
    await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
    await page.locator(".cover").first().click();

    // Then the bird holds the stage and its line is spoken — never silence.
    await expect(page.locator(".audio-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".audio-error .bird")).toBeVisible();
    await expect(page.locator(".audio-error .prompt")).toHaveText(
      "Oh! La storia fa un pisolino. Tocca l'uccellino per svegliarla.",
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
    await page.goto("/?theme=dusk");

    // Clouds, the line, no covers, no spinner.
    await expect(page.locator(".offline")).toBeVisible();
    await expect(page.locator(".offline .prompt")).toHaveText(
      "Le nuvole hanno preso le storie. Riprova tra poco!",
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
});
