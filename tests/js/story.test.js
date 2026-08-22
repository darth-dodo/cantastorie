import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { loadStory, orderPages } from "../../src/static/js/story.js";

// The dev fixture is the contract: story.json exactly as AI-357 pins it.
const fixture = JSON.parse(
  readFileSync("src/static/content/it/stories/la-barchetta-e-la-luna/story.json", "utf-8"),
);
const FIXTURE_URL = "/static/content/it/stories/la-barchetta-e-la-luna/story.json";

const fetchFixture = async () => ({ ok: true, json: async () => fixture });

// A tiny branching graph: shared prefix p1..p2, a choice on p2, and two arms
// a1..a2 / b1..b2. The player does not enforce PAGE_COUNT, so short graphs
// keep the path-walker tests readable.
const branchingJson = {
  schema_version: 1,
  id: "dev-branching",
  language: "it",
  title: "dev branching",
  shape: "branching",
  pages: [
    { id: "p1", text: "p1", audio: null, image: null, next_page: "p2", choice: null },
    {
      id: "p2",
      text: "p2",
      audio: null,
      image: null,
      next_page: null,
      choice: {
        prompt: "which way?",
        options: [
          { label: "a", card_image: null, audio: null, next_page: "a1" },
          { label: "b", card_image: null, audio: null, next_page: "b1" },
        ],
      },
    },
    { id: "a1", text: "a1", audio: null, image: null, next_page: "a2", choice: null },
    { id: "a2", text: "a2", audio: null, image: null, next_page: null, choice: null },
    { id: "b1", text: "b1", audio: null, image: null, next_page: "b2", choice: null },
    { id: "b2", text: "b2", audio: null, image: null, next_page: null, choice: null },
  ],
};
const fetchBranching = async () => ({ ok: true, json: async () => branchingJson });

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

describe("walking a branch arm (AI-428)", () => {
  it("pagesFrom walks an arm to its ending", async () => {
    const loaded = await loadStory("/dev-branching/story.json", fetchBranching);
    const arm = loaded.pagesFrom("b1");
    expect(arm.map((p) => p.id)).toEqual(["b1", "b2"]);
  });

  it("ordered walk still halts at the choice page", async () => {
    const loaded = await loadStory("/dev-branching/story.json", fetchBranching);
    expect(loaded.pages.map((p) => p.id)).toEqual(["p1", "p2"]);
  });
});
