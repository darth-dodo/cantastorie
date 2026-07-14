// Playback-loop acceptance scenarios (AI-364), named after the behaviors
// in docs/product.md -> "A Story Night, Start to Finish" and
// docs/architecture.md -> "Whole-story prefetch". The dev fixture story
// "La barchetta e la luna" narrates each page with a seconds-long chime,
// so the whole night runs hands-free in well under a minute.

import { expect, test } from "@playwright/test";

const STORY_BASE = "/static/content/it/stories/la-barchetta-e-la-luna/";

async function wakeAndOpenTheStory(page) {
  await page.goto("/?theme=dusk");
  // Given the shelf: the first tap anywhere wakes the sound...
  await page.locator(".greeting").click();
  await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
  // ...when the cover is tapped, the story opens.
  await page.locator(".cover").first().click();
  await expect(page.locator(".player")).toBeVisible();
}

test.describe("A Story Night, Start to Finish (product.md)", () => {
  test("story start prompt after the cover tap, then page 1 — \"Si parte! — and page 1 narration begins\"", async ({ page }) => {
    const promptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/story-start")) promptRequests.push(request.url());
    });

    await wakeAndOpenTheStory(page);

    // Then the story start prompt is fetched and spoken before page 1 turns.
    await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "0");
    expect(promptRequests.length).toBeGreaterThan(0);

    // And the full-bleed page shows the fixture story with progress dots.
    await expect(page.locator(".bead")).toHaveCount(8);
    await expect(page.locator(".caption span")).toContainText("la barchetta Nina");
  });

  test("pages turn themselves within 500 ms of the audio ending, hands-free to the end screen", async ({ page }) => {
    await wakeAndOpenTheStory(page);

    // Then, with no further taps, every page turn arrives on its own...
    for (let pageIndex = 1; pageIndex < 8; pageIndex++) {
      await expect(page.locator(".page-wash.current")).toHaveAttribute(
        "data-page",
        String(pageIndex),
        { timeout: 10_000 },
      );
    }

    // ...and the end screen offers replay and back-to-shelf pictures.
    await expect(page.locator(".end")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Ancora!" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Un'altra storia" })).toBeVisible();
  });

  test("the end prompt plays on the end screen, and replay starts the story over", async ({ page }) => {
    const endPromptRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/prompts/end")) endPromptRequests.push(request.url());
    });

    await wakeAndOpenTheStory(page);
    await expect(page.locator(".end")).toBeVisible({ timeout: 30_000 });
    expect(endPromptRequests.length).toBeGreaterThan(0);

    // When "Ancora!" is tapped, the story begins again from page 1...
    await page.getByRole("button", { name: "Ancora!" }).click();
    await expect(page.locator(".player")).toBeVisible();
    await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "0");

    // ...and keeps turning hands-free (replay reset, not a frozen player).
    await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "1", {
      timeout: 10_000,
    });
  });
});

test.describe("Whole-story prefetch (architecture.md -> The Player)", () => {
  test("every page's audio and image is fetched on the cover tap, before the story needs it", async ({ page }) => {
    const storyAssetRequests = new Set();
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith(STORY_BASE) && !url.pathname.endsWith("story.json")) {
        storyAssetRequests.add(url.pathname);
      }
    });

    await wakeAndOpenTheStory(page);

    // Then the prefetcher reports the whole story banked: 8 audio +
    // 8 images + the start, end, and audio-retry prompts.
    await page.waitForFunction(() => {
      const status = window.__shell?.prefetcher?.status();
      return status && status.loaded + status.failed >= status.total && status.total >= 19;
    });
    const status = await page.evaluate(() => window.__shell.prefetcher.status());
    expect(status.total).toBe(19);
    expect(status.failed).toBe(0);

    // And the requests really left while page 1 was still on screen.
    expect(storyAssetRequests.size).toBe(16);
    await expect(page.locator(".page-wash.current")).not.toHaveAttribute("data-page", "7");
  });
});
