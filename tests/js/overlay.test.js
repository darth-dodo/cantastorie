import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { init } from "../../src/static/js/main.js";

// The child player merges a family's private overlay shelf on top of the shared
// shelf when a family_token is present client-side. No token → shared only, and
// zero extra requests. An overlay fetch failure must never block bedtime: the
// shared shelf still renders. The child stays Clerk-free — the token comes from
// an injected reader (IndexedDB in prod), never a Clerk call.

const ASSET_BASE = "/static/content";
const sharedManifest = JSON.parse(readFileSync("src/static/content/it/manifest.json", "utf-8"));

const TOKEN = "a".repeat(32);

function overlayManifest() {
  return {
    language: "it",
    prompts: {},
    stories: [
      {
        id: "family-private-1",
        title: "La nostra storia privata",
        wash: "wash-barchetta",
        story: `${ASSET_BASE}/families/${TOKEN}/stories/family-private-1/story.json`,
        cover: `${ASSET_BASE}/families/${TOKEN}/stories/family-private-1/p1.webp`,
      },
    ],
  };
}

function fakeEngine() {
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
    async playNarration() {
      state = "playing";
    },
    pauseNarration() {
      state = "paused";
      return 0;
    },
    async resumeNarration() {
      state = "playing";
    },
    async playPrompt() {},
    stopAll() {
      state = "idle";
    },
    endNarration() {},
    endPrompt() {},
  };
}

let running = null;

afterEach(() => {
  running?.stop();
  running = null;
  localStorage.clear();
});

describe("family overlay merge", () => {
  it("appends the family's private stories when a token is present", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const urls = [];
    const fetchFn = async (url) => {
      urls.push(String(url));
      if (String(url).includes(`/families/${TOKEN}/`) && String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => overlayManifest() };
      }
      if (String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => sharedManifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };

    running = await init(document, {
      fetchFn,
      engine: fakeEngine(),
      readFamilyToken: async () => TOKEN,
    });

    const labels = [...document.querySelectorAll(".shelf .cover")].map((c) =>
      c.getAttribute("aria-label"),
    );
    expect(labels).toContain("La nostra storia privata");
    // shared stories still present
    for (const s of sharedManifest.stories) expect(labels).toContain(s.title);
    // the overlay manifest was actually requested (for the active language)
    expect(
      urls.some((u) => u.includes(`/families/${TOKEN}/`) && u.endsWith("manifest.json")),
    ).toBe(true);
  });

  it("makes zero overlay requests when no token is present", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const urls = [];
    const fetchFn = async (url) => {
      urls.push(String(url));
      if (String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => sharedManifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };

    running = await init(document, {
      fetchFn,
      engine: fakeEngine(),
      readFamilyToken: async () => null,
    });

    expect(running.manifestLoaded).toBe(true);
    expect(urls.some((u) => u.includes("/families/"))).toBe(false);
  });

  it("renders the shared shelf even when the overlay fetch fails", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const fetchFn = async (url) => {
      if (String(url).includes("/families/")) {
        throw new Error("overlay network error");
      }
      if (String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => sharedManifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };

    running = await init(document, {
      fetchFn,
      engine: fakeEngine(),
      readFamilyToken: async () => TOKEN,
    });

    // Never throws; the shared shelf is up.
    expect(running.manifestLoaded).toBe(true);
    const labels = [...document.querySelectorAll(".shelf .cover")].map((c) =>
      c.getAttribute("aria-label"),
    );
    for (const s of sharedManifest.stories) expect(labels).toContain(s.title);
    expect(labels).not.toContain("La nostra storia privata");
  });

  it("dedupes by id, shared shelf winning a collision", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const sharedId = sharedManifest.stories[0].id;
    const collidingOverlay = {
      language: "it",
      prompts: {},
      stories: [
        {
          id: sharedId,
          title: "OVERLAY SHOULD NOT WIN",
          wash: "wash-barchetta",
          story: `${ASSET_BASE}/families/${TOKEN}/stories/${sharedId}/story.json`,
          cover: `${ASSET_BASE}/families/${TOKEN}/stories/${sharedId}/p1.webp`,
        },
      ],
    };
    const fetchFn = async (url) => {
      if (String(url).includes(`/families/${TOKEN}/`) && String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => collidingOverlay };
      }
      if (String(url).endsWith("manifest.json")) {
        return { ok: true, json: async () => sharedManifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };

    running = await init(document, {
      fetchFn,
      engine: fakeEngine(),
      readFamilyToken: async () => TOKEN,
    });

    const labels = [...document.querySelectorAll(".shelf .cover")].map((c) =>
      c.getAttribute("aria-label"),
    );
    expect(labels).not.toContain("OVERLAY SHOULD NOT WIN");
    expect(labels).toContain(sharedManifest.stories[0].title);
  });
});
