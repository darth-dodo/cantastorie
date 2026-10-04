import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAudioEngine } from "../../src/static/js/audio-engine.js";
import { MANIFEST_FETCH_TIMEOUT_MS, init } from "../../src/static/js/main.js";

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
    // Always moving: these wiring specs never stall the voice (the stall
    // watchdog has its own specs in playback.test.js).
    position() {
      return performance.now() / 1000;
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

  it("defaults the child player to dusk by day too — whole app is dark by default (AI-459)", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 27, 12, 0)); // noon
    try {
      document.body.innerHTML = '<main id="app"></main>';
      running = await init(document, { fetchFn: manifestFetch });
      expect(document.documentElement.dataset.theme).toBe("dusk");
    } finally {
      vi.useRealTimers();
    }
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

  it("a dead manifest shows the clouds; when the sky clears, a tap brings the shelf (AI-367)", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let manifestUp = false;
    const flakyFetch = async (url) => {
      if (String(url).endsWith("manifest.json")) {
        if (!manifestUp) return { ok: false, status: 503 };
        return { ok: true, json: async () => manifest };
      }
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
    };
    const engine = fakeEngine();
    const promptUrls = [];
    const playPrompt = engine.playPrompt.bind(engine);
    engine.playPrompt = async (url, opts) => {
      promptUrls.push(url);
      return playPrompt(url, opts);
    };

    const pending = init(document, { fetchFn: flakyFetch, engine });

    // The clouds hold the boot: no shelf, no spinner, one big tap target.
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());
    expect(document.querySelector(".offline .prompt").textContent).toBe(
      "The clouds took the stories. Try again soon!",
    );
    expect(document.querySelector(".cover")).toBeNull();

    // A tap while still offline speaks the line and retries — clouds remain.
    document.querySelector(".offline").click();
    await vi.waitFor(() =>
      expect(promptUrls).toContain("/static/content/en/prompts/offline.wav"),
    );
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());

    // The network returns; the next tap loads the shelf and init resolves.
    manifestUp = true;
    document.querySelector(".offline").click();
    running = await pending;
    expect(running.manifestLoaded).toBe(true);
    expect(document.querySelectorAll(".shelf .cover").length).toBeGreaterThan(0);
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

  it("prev/next buttons turn pages on tap; prev hides on page 0", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine: fakeEngine() });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());

    const prev = document.querySelector(".nav-prev");
    const next = document.querySelector(".nav-next");
    expect(prev).not.toBeNull();
    expect(next).not.toBeNull();
    expect(prev.classList.contains("disabled")).toBe(true); // page 0: nothing behind

    next.click();
    await vi.waitFor(() =>
      expect(document.querySelector(".page-wash.current").dataset.page).toBe("1"),
    );
    expect(prev.classList.contains("disabled")).toBe(false);

    prev.click();
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

  it("shows the loaded story full-bleed: its art layer per page", async () => {
    const engine = fakeEngine();
    await openFirstCover(engine);
    expect(document.querySelectorAll(".page-art")).toHaveLength(8);
    expect(document.querySelector(".page-art.current")).not.toBeNull();
  });

  it("whole-story prefetch banks all 19 assets around the cover tap — pages and prompts", async () => {
    const engine = fakeEngine();
    await openFirstCover(engine);
    await vi.waitFor(() =>
      expect(running.prefetcher.status()).toEqual({ total: 19, loaded: 19, failed: 0 }),
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

describe("resume across a branch (AI-428)", () => {
  const branchingStory = JSON.parse(
    readFileSync("src/static/content/it/stories/dev-branching/story.json", "utf-8"),
  );

  // The dev-branching graph: shared p1..p6 (choice on p6), arm a = a1..a4,
  // arm b = b1..b4. pagesFrom("p1") halts at the choice (six pages); option 1
  // extends the played path with arm b, so an arm-b page sits at index 6..9.
  const branchingFetch = async (url) => {
    const path = String(url);
    if (path.endsWith("manifest.json")) return { ok: true, json: async () => manifest };
    if (path.includes("dev-branching")) return { ok: true, json: async () => branchingStory };
    if (path.endsWith("story.json")) return { ok: true, json: async () => storyFixture };
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
  };

  // The dev-branching cover is the last entry in the manifest.
  const branchingCover = () => [...document.querySelectorAll(".shelf .cover")].at(-1);

  it("replays the recorded choice, rebuilding the path so the saved page lands in the chosen arm", async () => {
    // A save left mid-arm-b: page 7 (b2 in the rebuilt path), option 1 picked.
    // Screen rests on the shelf so the cover is tappable — the reopen path.
    localStorage.setItem(
      "cantastorie-shell",
      JSON.stringify({ screen: "shelf", page: 7, choices: [1] }),
    );
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: branchingFetch, engine: fakeEngine() });

    branchingCover().click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));

    // Reopening an unfinished story offers resume; continue keeps the page.
    await vi.waitFor(() => expect(running.store.state.resumeOpen).toBe(true));
    running.store.resumeContinue();

    // The path was rebuilt (shared prefix + arm b = 10 pages) and the restored
    // index points into arm b, not off the un-extended six-page prefix.
    expect(running.store.state.pageCount).toBe(10);
    expect(running.store.state.page).toBe(7);
    expect(running.store.state.choices).toEqual([1]);
    expect(document.querySelectorAll(".page-art")).toHaveLength(10);
    expect(document.querySelector(".page-art.current").dataset.page).toBe("7");
  });

  it("discards a save whose recorded choice no longer fits the story graph — no crash, fresh start", async () => {
    // Option index 5 does not exist on the choice page: a republished story.
    localStorage.setItem(
      "cantastorie-shell",
      JSON.stringify({ screen: "shelf", page: 7, choices: [5] }),
    );
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: branchingFetch, engine: fakeEngine() });

    branchingCover().click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));

    // No resume offer for a discarded save: the story starts fresh at page 0.
    expect(running.store.state.resumeOpen).toBe(false);
    expect(running.store.state.page).toBe(0);
    expect(running.store.state.choices).toEqual([]);
  });
});

describe("resume across two branch points (M28, AI-494)", () => {
  // A story that branches twice: p1, p2 (choice: a1 | b1); arm a = a1, a2
  // (choice: c1 | d1); arm b = b1, b2; the second branch's arms are c1, c2
  // and d1, d2. No audio: the specs turn pages by hand.
  const page = (id, next_page, choice = null) => ({
    id,
    text: id,
    image: `${id}.webp`,
    next_page,
    choice,
  });
  const choice = (first, second) => ({
    prompt: "which way?",
    options: [
      { label: first, card_image: null, audio: null, next_page: first },
      { label: second, card_image: null, audio: null, next_page: second },
    ],
  });
  const twoBranchStory = {
    schema_version: 1,
    id: "dev-branching",
    language: "it",
    title: "Two branches",
    shape: "branching",
    pages: [
      page("p1", "p2"),
      page("p2", null, choice("a1", "b1")),
      page("a1", "a2"),
      page("a2", null, choice("c1", "d1")),
      page("b1", "b2"),
      page("b2", null),
      page("c1", "c2"),
      page("c2", null),
      page("d1", "d2"),
      page("d2", null),
    ],
  };
  const twoBranchFetch = async (url) => {
    const path = String(url);
    if (path.endsWith("manifest.json")) return { ok: true, json: async () => manifest };
    if (path.includes("dev-branching")) return { ok: true, json: async () => twoBranchStory };
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
  };
  const branchingCover = () => [...document.querySelectorAll(".shelf .cover")].at(-1);

  it("a save left mid-arm reaches the arm's own branch instead of cutting to the end", async () => {
    // Left on a1 (index 2 of p1, p2, a1, a2) after picking arm a.
    localStorage.setItem(
      "cantastorie-shell",
      JSON.stringify({ screen: "shelf", page: 2, choices: [0] }),
    );
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: twoBranchFetch, engine: fakeEngine() });

    branchingCover().click();
    await vi.waitFor(() => expect(running.store.state.resumeOpen).toBe(true));
    running.store.resumeContinue();
    expect(running.store.state.pageCount).toBe(4);
    // The next branch is a2's (index 3), not p2's — already behind the child.
    expect(running.store.state.choicePage).toBe(3);

    running.store.nextPage(); // a1 -> a2
    running.store.nextPage(); // a2's end opens the second branch
    expect(running.store.state.screen).toBe("player");
    expect(running.store.state.choiceOpen).toBe(true);

    await vi.waitFor(() => expect(document.querySelectorAll(".overlay .option")).toHaveLength(2));
    document.querySelectorAll(".overlay .option")[1].click(); // arm d
    await vi.waitFor(() => expect(running.store.state.choiceOpen).toBe(false));
    expect(running.store.state.page).toBe(4);
    expect(running.store.state.pageCount).toBe(6);
    expect(running.store.state.choices).toEqual([0, 1]);
  });
});

describe("replaying a branch in the same session (AI-482)", () => {
  const branchingStory = JSON.parse(
    readFileSync("src/static/content/it/stories/dev-branching/story.json", "utf-8"),
  );
  const branchingFetch = async (url) => {
    const path = String(url);
    if (path.endsWith("manifest.json")) return { ok: true, json: async () => manifest };
    if (path.includes("dev-branching")) return { ok: true, json: async () => branchingStory };
    if (path.endsWith("story.json")) return { ok: true, json: async () => storyFixture };
    return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
  };
  const branchingCover = () => [...document.querySelectorAll(".shelf .cover")].at(-1);

  // dev-branching: shared p1..p6 (choice on p6 = index 5), arm a = a1..a4
  // (a1's art is p7.*), arm b = b1..b4 (b1's art is p3.*). A branched path is
  // 6 + 4 = 10 pages and the arm's first page sits at index 6.
  const PREFIX_LENGTH = 6;
  const ARM_START = 6;
  const BRANCHED_LENGTH = 10;
  const artAt = (i) => document.querySelector(`.page-art[data-page="${i}"]`)?.style.backgroundImage;

  // Open the dev-branching cover from the shelf and start from page 0
  // (a leftover page from an earlier visit may offer resume — restart it).
  async function openFresh() {
    branchingCover().click();
    await vi.waitFor(() => expect(running.store.state.screen).toBe("player"));
    if (running.store.state.resumeOpen) running.store.resumeRestart();
    expect(running.store.state.page).toBe(0);
  }

  // Turn pages up to the choice and tap option i.
  async function pick(i) {
    while (!running.store.state.choiceOpen) running.store.nextPage();
    await vi.waitFor(() => expect(document.querySelectorAll(".overlay .option")).toHaveLength(2));
    document.querySelectorAll(".overlay .option")[i].click();
    await vi.waitFor(() => expect(running.store.state.choiceOpen).toBe(false));
  }

  it("picking the other arm on a reopen plays the shared prefix plus that arm only", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: branchingFetch, engine: fakeEngine() });

    await openFresh();
    await pick(0);
    expect(running.store.state.pageCount).toBe(BRANCHED_LENGTH);
    await vi.waitFor(() => expect(artAt(ARM_START)).toContain("p7.")); // a1

    running.store.exitStory(); // back to the shelf, same session (cached story)
    await openFresh();
    expect(running.store.state.pageCount).toBe(PREFIX_LENGTH); // pristine, not arm a
    await pick(1);

    expect(running.store.state.pageCount).toBe(BRANCHED_LENGTH);
    expect(running.store.state.page).toBe(ARM_START);
    await vi.waitFor(() => expect(artAt(ARM_START)).toContain("p3.")); // b1, not a1
    expect(document.querySelectorAll(".page-art")).toHaveLength(BRANCHED_LENGTH);
  });

  it("a resumed branched save, reopened again, never appends its arm twice", async () => {
    localStorage.setItem(
      "cantastorie-shell",
      JSON.stringify({ screen: "shelf", page: 7, choices: [1] }),
    );
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: branchingFetch, engine: fakeEngine() });

    branchingCover().click();
    await vi.waitFor(() => expect(running.store.state.resumeOpen).toBe(true));
    running.store.resumeContinue();
    expect(running.store.state.pageCount).toBe(BRANCHED_LENGTH);

    // Two more visits in the same session: each opens on the pristine story.
    for (const option of [1, 0]) {
      running.store.exitStory();
      await openFresh();
      expect(running.store.state.pageCount).toBe(PREFIX_LENGTH);
      await pick(option);
      expect(running.store.state.pageCount).toBe(BRANCHED_LENGTH);
      expect(running.store.state.page).toBe(ARM_START);
      await vi.waitFor(() => expect(artAt(ARM_START)).toContain(option === 1 ? "p3." : "p7."));
    }
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

describe("audio-error overlay (AI-367): the sleeping bird", () => {
  it("a narration failure shows the bird with the retry line; a tap retries", async () => {
    const engine = fakeEngine();
    let failNarration = true;
    const playNarration = engine.playNarration.bind(engine);
    engine.playNarration = async (url, opts) => {
      if (failNarration) throw new Error("audio fetch failed");
      return playNarration(url, opts);
    };

    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: routedFetch, engine });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    engine.endPrompt(); // "Si parte!" ends; page 1 narration fails

    await vi.waitFor(() => expect(document.querySelector(".audio-error")).not.toBeNull());
    expect(document.querySelector(".audio-error .bird")).not.toBeNull();
    expect(document.querySelector(".audio-error .prompt").textContent).toBe(
      "Oh! The story is taking a nap. Tap the bird to wake it up.",
    );

    failNarration = false;
    document.querySelector(".audio-error").click();
    await vi.waitFor(() => expect(document.querySelector(".audio-error")).toBeNull());
    expect(running.store.state.audioError).toBe(false);
    await vi.waitFor(() => expect(engine.state).toBe("playing"));
  });
});

describe("shelf settings (language + light)", () => {
  it("the gear opens the settings sheet with language tiles and light tiles", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    const gear = document.querySelector(".settings-gear");
    expect(gear).not.toBeNull();
    gear.click();
    const sheet = document.querySelector(".settings-sheet");
    expect(sheet).not.toBeNull();
    // 8 language tiles
    expect([...sheet.querySelectorAll(".settings-lang-tile")]).toHaveLength(8);
    // 3 light tiles
    expect([...sheet.querySelectorAll(".settings-light-tile")]).toHaveLength(3);
  });

  it("picking a language tile persists it and keeps the sheet open", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    const sheet = document.querySelector(".settings-sheet");
    const esTile = [...sheet.querySelectorAll(".settings-lang-tile")].find(
      (t) => t.getAttribute("aria-label") === "Español",
    );
    esTile.click();
    await vi.waitFor(() => {
      expect(localStorage.getItem("cantastorie-lang")).toBe("es");
      // sheet stays open until explicit close
      expect(document.querySelector(".settings-sheet")).not.toBeNull();
    });
  });

  it("selecting a light tile marks it selected", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    const sheet = document.querySelector(".settings-sheet");
    const lightTiles = [...sheet.querySelectorAll(".settings-light-tile")];
    // Click Evening (third tile)
    lightTiles[2].click();
    expect(lightTiles[2].classList.contains("selected")).toBe(true);
  });

  it("a light choice survives a reload, and its tile shows selected", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    // Click Day (first tile).
    document.querySelectorAll(".settings-light-tile")[0].click();
    expect(document.documentElement.dataset.theme).toBe("light");
    running.stop?.();

    // Reboot the player: the choice holds and Day is the selected tile.
    document.documentElement.dataset.theme = "";
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    expect(document.documentElement.dataset.theme).toBe("light");
    document.querySelector(".settings-gear").click();
    const tiles = [...document.querySelectorAll(".settings-light-tile")];
    expect(tiles.map((t) => t.classList.contains("selected"))).toEqual([true, false, false]);
  });

  it("By itself shows as selected once chosen", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    document.querySelectorAll(".settings-light-tile")[1].click();
    running.stop?.();
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    expect(document.querySelectorAll(".settings-light-tile")[1].classList.contains("selected")).toBe(true);
  });

  it("the close pill dismisses the sheet", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch, engine: fakeEngine() });
    document.querySelector(".settings-gear").click();
    document.querySelector(".settings-close-pill").click();
    expect(document.querySelector(".settings-sheet")).toBeNull();
  });
});

describe("wake wiring (AI-461): main.js greets on the first real activation", () => {
  it("a tap on the greeting header wakes the engine and speaks the greeting once", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const engine = fakeEngine();
    const playPrompt = engine.playPrompt.bind(engine);
    const promptUrls = [];
    engine.playPrompt = async (url, opts) => {
      promptUrls.push(url);
      return playPrompt(url, opts);
    };
    running = await init(document, { fetchFn: manifestFetch, engine });

    const greeting = document.querySelector(".greeting");
    greeting.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    greeting.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));
    greeting.click();

    await vi.waitFor(() => expect(engine.unlocked).toBe(true));
    // The greeting banks its buffer first, so it speaks a tick later.
    await vi.waitFor(() => expect(promptUrls).toEqual(["/static/content/it/prompts/greeting.wav"]));

    // A later, unrelated tap re-arms the wake but never greets a second time.
    document.querySelector(".settings-gear").click();
    expect(promptUrls).toEqual(["/static/content/it/prompts/greeting.wav"]);
  });

  it("a cover-first tap unlocks but never greets — the cover click starts the story instead", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const engine = fakeEngine();
    const playPrompt = engine.playPrompt.bind(engine);
    const promptUrls = [];
    engine.playPrompt = async (url, opts) => {
      promptUrls.push(url);
      return playPrompt(url, opts);
    };
    running = await init(document, { fetchFn: routedFetch, engine });

    document.querySelector(".cover").click();

    await vi.waitFor(() => expect(engine.unlocked).toBe(true));
    expect(promptUrls).not.toContain("/static/content/it/prompts/greeting.wav");
  });

  it("a greeting whose buffer lands after a story opened stays quiet, and the story still starts", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const engine = fakeEngine();
    const greetingUrl = "/static/content/it/prompts/greeting.wav";
    let landGreeting;
    const greetingLanded = new Promise((resolve) => {
      landGreeting = resolve;
    });
    // The real engine's playPrompt awaits the buffer, then silences any live
    // prompt without firing its onEnded. The fake already drops the earlier
    // prompt's onEnded; only the greeting's late buffer needs modelling.
    const load = engine.load.bind(engine);
    engine.load = async (url) => {
      if (url === greetingUrl) await greetingLanded;
      return load(url);
    };
    const playPrompt = engine.playPrompt.bind(engine);
    const promptUrls = [];
    engine.playPrompt = async (url, opts) => {
      if (url === greetingUrl) await greetingLanded;
      promptUrls.push(url);
      return playPrompt(url, opts);
    };
    running = await init(document, { fetchFn: routedFetch, engine });

    // A shelf tap wakes the engine; the greeting's buffer is still in flight.
    document.querySelector(".greeting").click();
    await vi.waitFor(() => expect(engine.unlocked).toBe(true));

    // The child taps a cover before it lands: "Si parte!" starts.
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(promptUrls).toContain(manifest.prompts.story_start));

    // Now the greeting's buffer lands — on the player, not the shelf.
    landGreeting();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(promptUrls).not.toContain(greetingUrl);

    // The start prompt ends and page 1's voice begins.
    engine.endPrompt();
    await vi.waitFor(() => expect(engine.state).toBe("playing"));
  });

  it("with the real engine and playback, a late greeting never freezes the story start", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const greetingUrl = "/static/content/it/prompts/greeting.wav";
    let landGreeting;
    const greetingLanded = new Promise((resolve) => {
      landGreeting = resolve;
    });
    // Each decoded buffer remembers its url, so a source can be told apart.
    const taggingFetch = async (url) => {
      const path = String(url);
      if (path === greetingUrl) await greetingLanded;
      if (path.endsWith("manifest.json") || path.endsWith("story.json")) return routedFetch(url);
      return { ok: true, arrayBuffer: async () => ({ url: path }) };
    };
    // A minimal Web Audio context, as in audio-engine.test.js. Like the
    // real one, stop() never fires onended synchronously; the spec fires the
    // start prompt's natural end itself.
    const ctx = {
      currentTime: 0,
      state: "suspended",
      destination: {},
      sources: [],
      async resume() {
        ctx.state = "running";
      },
      async decodeAudioData(data) {
        return { duration: 10, url: data.url };
      },
      createGain: () => ({
        gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} },
        connect() {},
      }),
      createBufferSource: () => {
        const source = { buffer: null, onended: null, connect() {}, start: vi.fn(), stop: vi.fn() };
        ctx.sources.push(source);
        return source;
      },
    };
    const engine = createAudioEngine({ createContext: () => ctx, fetchFn: taggingFetch });
    running = await init(document, { fetchFn: taggingFetch, engine });

    // A shelf tap wakes the engine; the greeting's buffer is still in flight.
    document.querySelector(".greeting").click();
    await vi.waitFor(() => expect(engine.unlocked).toBe(true));

    // A cover tap before it lands: "Si parte!" starts.
    document.querySelector(".cover").click();
    const startUrl = manifest.prompts.story_start;
    await vi.waitFor(() =>
      expect(ctx.sources.some((s) => s.buffer?.url === startUrl && s.start.mock.calls.length)).toBe(true),
    );

    // The greeting's buffer lands on the player.
    landGreeting();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(ctx.sources.some((s) => s.buffer?.url === greetingUrl)).toBe(false);

    // "Si parte!" reaches its natural end, and page 1's voice begins.
    const startSource = ctx.sources.find((s) => s.buffer?.url === startUrl);
    await startSource.onended();
    await vi.waitFor(() => expect(engine.state).toBe("playing"));
    expect(running.store.state.screen).toBe("player");
  });
});

describe("a network that never answers (B8, AI-473)", () => {
  // Accepts the request, never responds, but honors an abort signal the way
  // a real fetch does: captive portal, hotel Wi-Fi, a half-dead radio.
  const hang = (_url, { signal } = {}) =>
    new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(signal.reason));
    });

  it("the manifest fetch carries an abort signal bounded by MANIFEST_FETCH_TIMEOUT_MS", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const signals = [];
    const spyFetch = async (url, opts) => {
      if (String(url).endsWith("manifest.json")) signals.push(opts?.signal ?? null);
      return manifestFetch(url);
    };
    running = await init(document, { fetchFn: spyFetch, engine: fakeEngine() });
    expect(signals).toHaveLength(1);
    expect(signals[0]).toBeInstanceOf(AbortSignal);
    expect(MANIFEST_FETCH_TIMEOUT_MS).toBe(8000);
  });

  it("a hung manifest times out into the clouds; a tap once it answers brings the shelf", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let manifestUp = false;
    const fetchFn = (url, opts) => {
      if (String(url).endsWith("manifest.json") && !manifestUp) return hang(url, opts);
      return routedFetch(url);
    };

    const pending = init(document, { fetchFn, engine: fakeEngine(), manifestTimeoutMs: 30 });

    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());
    expect(document.querySelector(".cover")).toBeNull();

    manifestUp = true;
    document.querySelector(".offline").click();
    running = await pending;
    expect(running.manifestLoaded).toBe(true);
    expect(document.querySelectorAll(".shelf .cover").length).toBeGreaterThan(0);
  });

  it("a hung family overlay times out and the shared shelf still renders", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const fetchFn = (url, opts) => {
      if (String(url).includes("/families/")) return hang(url, opts);
      return routedFetch(url);
    };
    running = await init(document, {
      fetchFn,
      engine: fakeEngine(),
      manifestTimeoutMs: 30,
      readFamilyToken: async () => "0123456789abcdef0123456789abcdef",
    });
    expect(running.manifestLoaded).toBe(true);
    expect(document.querySelectorAll(".shelf .cover")).toHaveLength(manifest.stories.length);
  });

  it("a hung published story shimmers, then the clouds speak on the shelf; a tap retries that story", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let storyUp = false;
    let storyJsonFetches = 0;
    const fetchFn = (url, opts) => {
      if (String(url).endsWith("story.json")) {
        storyJsonFetches += 1;
        if (!storyUp) return hang(url, opts);
      }
      return routedFetch(url);
    };
    const engine = fakeEngine();
    const promptUrls = [];
    const playPrompt = engine.playPrompt.bind(engine);
    engine.playPrompt = async (url, opts) => {
      promptUrls.push(url);
      return playPrompt(url, opts);
    };
    running = await init(document, { fetchFn, engine, storyTimeoutMs: 50 });
    const savedBefore = localStorage.getItem("cantastorie-shell");

    // The tap shows the cover is working on it, not an inert button.
    document.querySelector(".cover").click();
    expect(document.querySelector(".cover").classList.contains("loading")).toBe(true);

    // The timeout never plays the silent mock story: the clouds take the
    // stage, speak their line, and the child is still on the shelf.
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());
    expect(document.querySelector(".player")).toBeNull();
    expect(running.store.state.screen).toBe("shelf");
    expect(running.playback.hasStory()).toBe(false);
    await vi.waitFor(() => expect(promptUrls).toContain("/static/content/en/prompts/offline.wav"));

    // The failed attempt saved no progress for that story.
    expect(localStorage.getItem("cantastorie-shell")).toBe(savedBefore);

    // Still hung: a tap retries and, on the next timeout, fresh clouds come back.
    const firstClouds = document.querySelector(".offline");
    firstClouds.click();
    await vi.waitFor(() => expect(storyJsonFetches).toBe(2));
    await vi.waitFor(() => {
      const clouds = document.querySelector(".offline");
      expect(clouds).not.toBeNull();
      expect(clouds).not.toBe(firstClouds);
    });

    // The network returns: the next tap on the clouds opens that very story.
    storyUp = true;
    document.querySelector(".offline").click();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    expect(storyJsonFetches).toBe(3);
    await vi.waitFor(() => expect(document.querySelector(".player")).not.toBeNull());
    expect(document.querySelector(".bead")).not.toBeNull();
  });

  it("the clouds shimmer while a cover retry is pending, and stop when it settles", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let mode = "hang"; // then "gated", then released
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const fetchFn = async (url, opts) => {
      if (String(url).endsWith("story.json")) {
        if (mode === "hang") return hang(url, opts);
        await gate;
      }
      return routedFetch(url);
    };
    running = await init(document, { fetchFn, engine: fakeEngine(), storyTimeoutMs: 50 });
    document.querySelector(".cover").click();
    await vi.waitFor(() => expect(document.querySelector(".offline")).not.toBeNull());
    const clouds = document.querySelector(".offline");
    expect(clouds.classList.contains("loading")).toBe(false);

    // The retry tap is answered at once: the clouds shimmer while it loads.
    mode = "gated";
    clouds.click();
    expect(clouds.classList.contains("loading")).toBe(true);

    release();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    expect(clouds.classList.contains("loading")).toBe(false);
  });

  it("a double tap on a gated load opens the story once, and replays the saved path once", async () => {
    const branchingStory = JSON.parse(
      readFileSync("src/static/content/it/stories/dev-branching/story.json", "utf-8"),
    );
    // A save left mid-arm-b, so each open would fold the pick over loaded.pages.
    localStorage.setItem("cantastorie-shell", JSON.stringify({ screen: "shelf", page: 7, choices: [1] }));
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const fetchFn = async (url) => {
      const path = String(url);
      if (path.includes("dev-branching")) {
        await gate;
        return { ok: true, json: async () => branchingStory };
      }
      return routedFetch(url);
    };
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn, engine: fakeEngine() });
    const openStory = vi.spyOn(running.playback, "openStory");

    const cover = [...document.querySelectorAll(".shelf .cover")].at(-1);
    cover.click();
    cover.click();
    release();

    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(openStory).toHaveBeenCalledTimes(1);
    // Six shared pages plus the four-page arm b, folded over exactly once.
    expect(openStory.mock.calls[0][0].pages).toHaveLength(10);
  });

  it("a tap on a second cover while the first loads opens only the second", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const branchingStory = JSON.parse(
      readFileSync("src/static/content/it/stories/dev-branching/story.json", "utf-8"),
    );
    const fetchFn = async (url) => {
      const path = String(url);
      if (path.includes("la-barchetta") && path.endsWith("story.json")) await gate; // the first cover is slow
      if (path.includes("dev-branching")) return { ok: true, json: async () => branchingStory };
      return routedFetch(url);
    };
    running = await init(document, { fetchFn, engine: fakeEngine() });
    const openStory = vi.spyOn(running.playback, "openStory");
    const covers = [...document.querySelectorAll(".shelf .cover")];
    covers[0].click(); // la barchetta, gated
    covers.at(-1).click(); // dev-branching, answers at once
    await vi.waitFor(() => expect(openStory).toHaveBeenCalledTimes(1));
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(openStory).toHaveBeenCalledTimes(1);
    expect(openStory.mock.calls[0][0].id).toBe("dev-branching");
  });

  it("a language switch during a cover load drops the stale open", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const fetchFn = async (url) => {
      if (String(url).endsWith("story.json")) await gate;
      return routedFetch(url);
    };
    running = await init(document, { fetchFn, engine: fakeEngine() });
    const oldPlayback = running.playback;
    const openStory = vi.spyOn(oldPlayback, "openStory");
    document.querySelector(".cover").click();
    await running.switchLanguage("es");
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(openStory).not.toHaveBeenCalled();
    expect(running.store.state.screen).toBe("shelf");
  });

  it("a successful load clears the cover shimmer", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const fetchFn = async (url) => {
      if (String(url).endsWith("story.json")) await gate;
      return routedFetch(url);
    };
    running = await init(document, { fetchFn, engine: fakeEngine() });
    const cover = document.querySelector(".cover");
    cover.click();
    expect(cover.classList.contains("loading")).toBe(true);
    release();
    await vi.waitFor(() => expect(running.playback.hasStory()).toBe(true));
    expect(cover.classList.contains("loading")).toBe(false);
  });
});
