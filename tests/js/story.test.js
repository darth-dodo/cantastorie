import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadStory, orderPages } from "../../src/static/js/story.js";

// The dev fixture is the contract: story.json exactly as AI-357 pins it.
const fixture = JSON.parse(
  readFileSync("src/static/content/it/stories/la-barchetta-e-la-luna/story.json", "utf-8"),
);
const FIXTURE_URL = "/static/content/it/stories/la-barchetta-e-la-luna/story.json";

const fetchFixture = async () => ({ ok: true, json: async () => fixture });

describe("loading a story.json (schema pinned by AI-357)", () => {
  it("orders the heard path by following next_page links, not array order", () => {
    // Given the pages arrive shuffled on the wire...
    const shuffled = { ...fixture, pages: [...fixture.pages].reverse() };
    // ...when the path is ordered from the entry page...
    const ordered = orderPages(shuffled);
    // ...then the child still hears p1 through p8.
    expect(ordered.map((p) => p.id)).toEqual(["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"]);
  });

  it("resolves every audio and image URL relative to the story.json location", async () => {
    const story = await loadStory(FIXTURE_URL, fetchFixture);
    const base = "/static/content/it/stories/la-barchetta-e-la-luna/";
    for (const page of story.pages) {
      expect(page.audioUrl).toBe(base + fixture.pages.find((p) => p.id === page.id).audio.file);
      expect(page.imageUrl).toMatch(new RegExp(`^${base}${page.id}\\.`));
    }
  });

  it("keeps text, timings, and the (null) choice for each page", async () => {
    const story = await loadStory(FIXTURE_URL, fetchFixture);
    expect(story.title).toBe("La barchetta e la luna");
    expect(story.pages).toHaveLength(8);
    expect(story.pages[0].text).toContain("la barchetta Nina");
    expect(story.pages[0].timings.length).toBeGreaterThan(0);
    story.pages.forEach((page) => expect(page.choice).toBeNull());
  });

  it("rejects an unrecognized schema so the shell can fall back to the page timer", async () => {
    const wrongVersion = async () => ({ ok: true, json: async () => ({ schema_version: 2 }) });
    await expect(loadStory(FIXTURE_URL, wrongVersion)).rejects.toThrow(/story/i);

    const notFound = async () => ({ ok: false, status: 404 });
    await expect(loadStory(FIXTURE_URL, notFound)).rejects.toThrow(/404/);
  });
});
