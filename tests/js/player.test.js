import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { init } from "../../src/static/js/main.js";

// Vitest runs with cwd at the project root; import.meta.url is an http://
// URL inside the jsdom environment, so resolve from cwd instead. The FastAPI
// shell serves this template at "/" and mounts the assets under "/static".
// The server renders {{ asset_base }} from Settings.asset_base (the R2 public
// URL in prod, the static mount in dev); Vitest has no Jinja, so mirror that
// substitution with the dev default here.
const ASSET_BASE = "/static/content";
const indexHtml = readFileSync("src/templates/index.html", "utf-8").replace(
  "{{ asset_base }}",
  ASSET_BASE,
);
const manifest = JSON.parse(readFileSync("src/static/content/it/manifest.json", "utf-8"));
const storyFixture = JSON.parse(
  readFileSync("src/static/content/it/stories/la-barchetta-e-la-luna/story.json", "utf-8"),
);

const manifestFetch = async () => ({ ok: true, json: async () => manifest });
const brokenFetch = async () => ({ ok: false, status: 503 });

// The full dev content tree: manifest, story.json, and byte assets.
const routedFetch = async (url) => {
  const path = String(url);
  if (path.endsWith("manifest.json")) return { ok: true, json: async () => manifest };
  if (path.endsWith("story.json")) return { ok: true, json: async () => storyFixture };
  return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
};

// jsdom has no AudioContext; init takes an injected engine for the same
// reason the engine takes injected factories.
function fakeEngine() {
  let narration = null;
  let prompt = null;
  let held = null;
  let state = "idle";
  return {
    get state() {
      return state;
    },
    unlocked: false,
    async unlock() {
      this.unlocked = true;
    },
    async load() {},
    async playNarration(url, { onEnded } = {}) {
      narration = { url, onEnded };
      held = null;
      state = "playing";
    },
    pauseNarration() {
      held = narration;
      narration = null;
      state = "paused";
      return 0;
    },
    async resumeNarration() {
      if (!held) return;
      narration = held;
      held = null;
      state = "playing";
    },
    async playPrompt(url, { onEnded } = {}) {
      prompt = { url, onEnded };
    },
    stopAll() {
      narration = prompt = held = null;
      state = "idle";
    },
    endNarration() {
      const finished = narration;
      narration = null;
      state = "idle";
      finished?.onEnded?.();
    },
    endPrompt() {
      const finished = prompt;
      prompt = null;
      finished?.onEnded?.();
    },
  };
}

let running = null;

afterEach(() => {
  running?.stop();
  running = null;
  localStorage.clear();
});

describe("player shell", () => {
  it("index.html mounts an #app root, the stylesheets, and the asset base", () => {
    document.documentElement.innerHTML = indexHtml;
    expect(document.querySelector("#app")).not.toBeNull();
    expect(document.querySelector('link[href="/static/css/tokens.css"]')).not.toBeNull();
    expect(document.querySelector('link[href="/static/css/player.css"]')).not.toBeNull();
    expect(document.querySelector('meta[name="asset-base"]').content).toBe(ASSET_BASE);
  });

  it("boots a manifest-driven shelf", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch });
    expect(running.manifestLoaded).toBe(true);
    const covers = [...document.querySelectorAll(".shelf .cover")];
    expect(covers.map((c) => c.getAttribute("aria-label"))).toEqual(
      manifest.stories.map((s) => s.title),
    );
  });

  it("falls back to the built-in covers when the manifest is unreachable", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: brokenFetch });
    expect(running.manifestLoaded).toBe(false);
    expect(document.querySelectorAll(".shelf .cover")).toHaveLength(4);
  });

  it("a cover tap opens the player with beads and the play-pause control", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine: fakeEngine() });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());
    expect(document.querySelectorAll(".bead")).toHaveLength(8);
    expect(document.querySelector(".play-pause")).not.toBeNull();
    expect(document.querySelector(".page-wash.current").dataset.page).toBe("0");
  });
});

describe("the wired playback loop (cover tap -> prompt -> narration turns the pages)", () => {
  async function openFirstCover(engine) {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());
  }

  it("shows the loaded story full-bleed: its text as the caption, its art layer per page", async () => {
    const engine = fakeEngine();
    await openFirstCover(engine);
    expect(document.querySelector(".caption span").textContent).toContain("la barchetta Nina");
    expect(document.querySelectorAll(".page-art")).toHaveLength(8);
    expect(document.querySelector(".page-art.current")).not.toBeNull();
  });

  it("whole-story prefetch banks all 18 assets around the cover tap — pages and prompts", async () => {
    const engine = fakeEngine();
    await openFirstCover(engine);
    await vi.waitFor(() =>
      expect(running.prefetcher.status()).toEqual({ total: 18, loaded: 18, failed: 0 }),
    );
  });

  it("a double-tapped cover fetches story.json once: the promise is the cache", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let storyJsonFetches = 0;
    const countingFetch = async (url) => {
      if (String(url).endsWith("story.json")) storyJsonFetches += 1;
      return routedFetch(url);
    };
    running = await init(document, { fetchFn: countingFetch, engine: fakeEngine() });

    // The excited double-tap: two clicks before the first load resolves.
    const cover = document.querySelector(".cover");
    cover.click();
    cover.click();

    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    expect(storyJsonFetches).toBe(1);
  });

  it("the start prompt ends, narration begins, and audio end turns the page", async () => {
    const engine = fakeEngine();
    await openFirstCover(engine);
    engine.endPrompt(); // "Si parte!"
    await vi.waitFor(() => expect(engine.state).toBe("playing"));
    engine.endNarration(); // page 1's voice reaches its natural end
    expect(document.querySelector(".page-wash.current").dataset.page).toBe("1");
    expect(document.querySelector(".bead.past")).not.toBeNull();
  });

  it("a cover without a story.json keeps the page-timer stand-in", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine: fakeEngine() });
    document.querySelectorAll(".cover")[1].click(); // panetteria: no story yet
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());
    expect(running.playback.hasStory()).toBe(false);
    running.store.advance(); // what the timer does
    expect(running.store.state.page).toBe(1);
  });
});

describe("published shelf: cross-origin R2 manifest", () => {
  const r2Manifest = {
    language: "en",
    prompts: {
      greeting: "https://pub-test.r2.dev/published/prompts/en/shelf_greeting.abc123.mp3",
      story_start: "https://pub-test.r2.dev/published/prompts/en/story_start.abc123.mp3",
      end: "https://pub-test.r2.dev/published/prompts/en/end_prompt.abc123.mp3",
    },
    stories: [
      {
        id: "animals-helping-each-other-en-0397c7d0",
        title: "The Helpful Friends",
        wash: "wash-bosco",
        story: "https://pub-test.r2.dev/published/stories/animals-helping-each-other-en-0397c7d0/story.json",
      },
    ],
  };

  const r2Fetch = async (url) => {
    const path = String(url);
    if (path.endsWith("manifest.json")) return { ok: true, json: async () => r2Manifest };
    if (path.endsWith("story.json")) return { ok: true, json: async () => storyFixture };
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
  };

  it("boots from an R2-shaped manifest with absolute cross-origin URLs", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: r2Fetch, engine: fakeEngine() });
    expect(running.manifestLoaded).toBe(true);
    const covers = [...document.querySelectorAll(".shelf .cover")];
    expect(covers.map((c) => c.getAttribute("aria-label"))).toEqual(["The Helpful Friends"]);
    expect(covers[0].classList.contains("wash-bosco")).toBe(true);
  });

  it("a cover tap loads story.json from the R2 URL and renders 8 beads", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: r2Fetch, engine: fakeEngine() });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());
    expect(running.playback.hasStory()).toBe(true);
    expect(document.querySelectorAll(".bead")).toHaveLength(8);
  });
});
