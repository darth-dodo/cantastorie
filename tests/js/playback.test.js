import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPlayback, STORY_START_LOAD_TIMEOUT_MS } from "../../src/static/js/playback.js";
import { createStore } from "../../src/static/js/store.js";

// Playback-loop specs (AI-364), named for the behaviors in docs/product.md
// -> "A Story Night, Start to Finish": the story start prompt after the
// cover tap, pages that turn themselves within 500 ms of the audio ending
// with a gentle crossfade, the end screen with its end prompt, and replay.

// A fake audio engine with the real engine's interface. Crossfades and
// duck rules are the real engine's own tested behavior (audio-engine
// .test.js); here we watch what playback asks of it.
function fakeEngine() {
  const calls = [];
  const stalledLoads = new Map();
  let narration = null;
  let held = null;
  let prompt = null;
  let state = "idle";
  return {
    calls,
    get state() {
      return state;
    },
    // Test driver: make one URL's load hang until the returned release fires.
    stallLoad(url) {
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      stalledLoads.set(url, gate);
      return () => {
        stalledLoads.delete(url);
        release();
      };
    },
    async load(url) {
      calls.push(["load", url]);
      const gate = stalledLoads.get(url);
      if (gate) await gate;
    },
    async playNarration(url, { onEnded } = {}) {
      calls.push(["narration", url]);
      narration = { url, onEnded };
      held = null;
      state = "playing";
    },
    pauseNarration() {
      if (narration) held = narration;
      narration = null;
      state = "paused";
      return 1.5;
    },
    async resumeNarration() {
      if (!held) return;
      calls.push(["resume", held.url]);
      narration = held;
      held = null;
      state = "playing";
    },
    async playPrompt(url, { onEnded } = {}) {
      calls.push(["prompt", url]);
      prompt = { url, onEnded };
    },
    stopAll() {
      calls.push(["stopAll"]);
      narration = null;
      held = null;
      prompt = null;
      state = "idle";
    },
    // Test drivers: the audio clock.
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

function fixtureStory(pageCount = 8) {
  return {
    id: "la-barchetta-e-la-luna",
    title: "La barchetta e la luna",
    pages: Array.from({ length: pageCount }, (_, i) => ({
      id: `p${i + 1}`,
      text: `pagina ${i + 1}`,
      audioUrl: `/s/p${i + 1}.wav`,
      imageUrl: `/s/p${i + 1}.webp`,
      choice: null,
    })),
  };
}

const PROMPTS = {
  story_start: "/p/story-start.wav",
  end: "/p/end.wav",
  audio_retry: "/p/audio-retry.wav",
};

let store;
let engine;
let prefetcher;
let playback;

beforeEach(() => {
  store = createStore();
  engine = fakeEngine();
  prefetcher = { prefetchStory: vi.fn(async () => ({ total: 16, loaded: 16, failed: 0 })) };
  playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
});

async function openFresh(story = fixtureStory()) {
  await playback.openStory(story);
  return story;
}

function narrations() {
  return engine.calls.filter(([kind]) => kind === "narration").map(([, url]) => url);
}

function promptsSpoken() {
  return engine.calls.filter(([kind]) => kind === "prompt").map(([, url]) => url);
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("Story start prompt — \"Tap a cover. 'Si parte!' — and page 1 narration begins\"", () => {
  it("given a fresh story, the start prompt speaks first and page 1 narration follows it", async () => {
    // When the cover opens the story...
    await openFresh();

    // ...then the prompt was requested and narration is still held back...
    expect(engine.calls).toContainEqual(["prompt", PROMPTS.story_start]);
    expect(narrations()).toEqual([]);

    // ...and when the prompt finishes, page 1 narration begins.
    engine.endPrompt();
    expect(narrations()).toEqual(["/s/p1.wav"]);
    expect(store.state).toMatchObject({ screen: "player", page: 0, playing: true });
  });

  it("whole-story prefetch starts on the cover tap, prompts included, before page 1 plays", async () => {
    const story = await openFresh();
    // The end prompt banks with the story: by the end screen it is local.
    expect(prefetcher.prefetchStory).toHaveBeenCalledWith(story, [
      PROMPTS.story_start,
      PROMPTS.end,
      PROMPTS.audio_retry,
    ]);
  });

  it("a missing start prompt never blocks the story: narration begins anyway", async () => {
    playback = createPlayback({ store, engine, prefetcher, prompts: {} });
    await playback.openStory(fixtureStory());
    expect(narrations()).toEqual(["/s/p1.wav"]);
  });
});

describe("Auto page turn — \"Pages turn themselves within 500 ms of the audio ending, with a gentle crossfade\"", () => {
  beforeEach(async () => {
    await openFresh();
    engine.endPrompt();
  });

  it("turns the page immediately when the page audio ends — well inside the 500 ms budget", () => {
    vi.useFakeTimers();
    try {
      // When page 1's narration reaches its natural end...
      engine.endNarration();
      // ...then, with zero timers elapsed, the store shows page 2 and its
      // narration was already requested.
      expect(store.state.page).toBe(1);
      expect(narrations()).toEqual(["/s/p1.wav", "/s/p2.wav"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("the turn is a crossfade, not a stop: playback never silences the engine between pages", () => {
    engine.endNarration();
    // The engine crossfades when a new narration overlaps the old fade —
    // so the one thing playback must NOT do between pages is stopAll.
    expect(engine.calls.map(([kind]) => kind)).not.toContain("stopAll");
  });

  it("carries the story hands-free from page 1 to the end screen", () => {
    for (let i = 0; i < 8; i++) engine.endNarration();
    expect(store.state.screen).toBe("end");
    expect(narrations()).toEqual([
      "/s/p1.wav",
      "/s/p2.wav",
      "/s/p3.wav",
      "/s/p4.wav",
      "/s/p5.wav",
      "/s/p6.wav",
      "/s/p7.wav",
      "/s/p8.wav",
    ]);
  });
});

describe("Pause and resume — \"pausing and resuming continues from the exact position\"", () => {
  beforeEach(async () => {
    await openFresh();
    engine.endPrompt();
  });

  it("the play-pause blob holds narration at its exact position and resumes it, not restarts it", () => {
    // When the child taps pause...
    store.togglePlay();
    expect(engine.state).toBe("paused");

    // ...and taps play again...
    store.togglePlay();

    // ...then the engine RESUMED the held voice; no fresh narration started.
    expect(engine.calls).toContainEqual(["resume", "/s/p1.wav"]);
    expect(narrations()).toEqual(["/s/p1.wav"]);
  });

  it("audio that ends while paused does not turn the page", () => {
    store.togglePlay();
    store.advance(); // a stray advance while paused (store guards it)
    expect(store.state.page).toBe(0);
  });
});

describe("End screen — final scene, replay and back-to-shelf pictures, end prompt", () => {
  beforeEach(async () => {
    await openFresh();
    engine.endPrompt();
    for (let i = 0; i < 8; i++) engine.endNarration();
    await flush(); // the end prompt plays once its (cached) load settles
  });

  it("when the final page's audio ends, the end prompt speaks", () => {
    expect(store.state.screen).toBe("end");
    expect(engine.calls).toContainEqual(["prompt", PROMPTS.end]);
  });

  it("replay resets to page 1 and the story plays again without re-prefetching", () => {
    // When "Ancora!" is tapped...
    store.replay();

    // ...then page 1 narrates again from the top...
    expect(store.state).toMatchObject({ screen: "player", page: 0, playing: true });
    expect(narrations().at(-1)).toBe("/s/p1.wav");

    // ...and the prefetch bookkeeping was not asked to start over.
    expect(prefetcher.prefetchStory).toHaveBeenCalledTimes(1);

    // The replayed story still turns itself.
    engine.endNarration();
    expect(store.state.page).toBe(1);
  });

  it("back to the shelf stops every voice", () => {
    store.toShelf();
    expect(engine.calls.map(([kind]) => kind)).toContain("stopAll");
    expect(engine.state).toBe("idle");
  });
});

describe("Coming back — the resume offer (product.md)", () => {
  it("reopening an unfinished story offers resume with no start prompt and no narration", async () => {
    // Given the child left mid-story...
    store = createStore({ page: 3 });
    engine = fakeEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });

    // ...when the cover is tapped again...
    await playback.openStory(fixtureStory());

    // ...then the resume offer shows, silent until a picture is tapped.
    expect(store.state.resumeOpen).toBe(true);
    expect(narrations()).toEqual([]);
    expect(engine.calls).not.toContainEqual(["prompt", PROMPTS.story_start]);

    // "Continuiamo" narrates the page the child left...
    store.resumeContinue();
    expect(narrations()).toEqual(["/s/p4.wav"]);
  });

  it("\"Ricominciamo\" starts narration over from page 1", async () => {
    store = createStore({ page: 3 });
    engine = fakeEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());

    store.resumeRestart();
    expect(narrations()).toEqual(["/s/p1.wav"]);
  });
});

describe("Choice pages stay possible (playback ignores them; AI-370 owns the overlay)", () => {
  it("audio end on a choice page opens the overlay and starts no narration", async () => {
    const story = fixtureStory();
    story.pages[2].choice = { options: [{ next_page: "p4" }, { next_page: "p4" }] };
    await playback.openStory(story);
    engine.endPrompt();

    engine.endNarration(); // p1 -> p2
    engine.endNarration(); // p2 -> p3 (the choice page narrates)
    expect(narrations().at(-1)).toBe("/s/p3.wav");

    engine.endNarration(); // p3 audio ends -> the choice opens, no turn
    expect(store.state).toMatchObject({ page: 2, choiceOpen: true });
    expect(narrations()).toHaveLength(3);

    // Choosing closes the overlay and narration carries on.
    store.choose();
    expect(narrations().at(-1)).toBe("/s/p4.wav");
  });
});

describe("Race interleavings (PR #8 review)", () => {
  it("a double-tapped cover speaks one start prompt sequence and starts page 1 exactly once", async () => {
    // Given an excited double-tap: both openStory calls run...
    await playback.openStory(fixtureStory());
    await playback.openStory(fixtureStory());
    expect(promptsSpoken()).toEqual([PROMPTS.story_start, PROMPTS.story_start]);

    // ...when the surviving prompt ends (the engine silenced the first,
    // and a silenced prompt keeps its onEnded to itself)...
    engine.endPrompt();

    // ...then page 1 narration begins exactly once — never over the prompt.
    expect(narrations()).toEqual(["/s/p1.wav"]);
  });

  it("an end prompt that finishes loading after 'Ancora!' stays quiet — it must not duck the replay", async () => {
    // Given a cold cache where the end prompt is still on the wire...
    const releaseEndPrompt = engine.stallLoad(PROMPTS.end);
    await openFresh();
    engine.endPrompt();
    for (let i = 0; i < 8; i++) engine.endNarration();
    expect(store.state.screen).toBe("end");

    // ...when the child taps replay before it arrives...
    store.replay();
    expect(narrations().at(-1)).toBe("/s/p1.wav");

    // ...then the late-arriving prompt checks the screen and stays quiet.
    releaseEndPrompt();
    await flush();
    expect(promptsSpoken()).not.toContain(PROMPTS.end);
  });

  it("the end prompt still speaks when the child stays on the end screen", async () => {
    const releaseEndPrompt = engine.stallLoad(PROMPTS.end);
    await openFresh();
    engine.endPrompt();
    for (let i = 0; i < 8; i++) engine.endNarration();

    releaseEndPrompt();
    await flush();
    expect(promptsSpoken()).toContain(PROMPTS.end);
  });

  it("a hung start-prompt load frees the story after the timeout instead of freezing it mute", async () => {
    vi.useFakeTimers();
    try {
      // Given the start prompt's request hangs forever...
      engine.stallLoad(PROMPTS.story_start);
      const opening = playback.openStory(fixtureStory());

      // ...when the timeout passes...
      await vi.advanceTimersByTimeAsync(STORY_START_LOAD_TIMEOUT_MS);
      await opening;

      // ...then the pages speak without their fanfare — never dead air.
      expect(narrations()).toEqual(["/s/p1.wav"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("exiting to the shelf while the start prompt loads leaves the shelf silent", async () => {
    const releaseStart = engine.stallLoad(PROMPTS.story_start);
    const opening = playback.openStory(fixtureStory());

    // The child bails out to the shelf while the prompt is on the wire.
    store.exitStory();
    releaseStart();
    await opening;

    expect(promptsSpoken()).toEqual([]);
    expect(narrations()).toEqual([]);
  });
});

describe("Stories the pipeline hasn't produced yet", () => {
  it("clearStory hands the reins back to the page timer", async () => {
    await openFresh();
    expect(playback.hasStory()).toBe(true);
    playback.clearStory();
    expect(playback.hasStory()).toBe(false);
  });
});

describe("Audio won't load (AI-367) — the bird speaks, a tap wakes the story", () => {
  // The file's fakeEngine never fails; wrap it so the first `failures`
  // narrations reject the way a dead network makes the real engine reject.
  function failingEngine(failures = Infinity) {
    const wrapped = fakeEngine();
    let remaining = failures;
    const playNarration = wrapped.playNarration;
    wrapped.playNarration = async (url, opts) => {
      if (remaining > 0) {
        remaining -= 1;
        wrapped.calls.push(["narration", url]); // the attempt happened; the wire died
        throw new Error(`audio fetch failed: ${url}`);
      }
      return playNarration(url, opts);
    };
    return wrapped;
  }

  it("a narration load failure flips the store to audioError and speaks the retry prompt", async () => {
    engine = failingEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt(); // "Si parte!" ends; page 1's voice rejects
    await flush();

    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).toContain(PROMPTS.audio_retry);
  });

  it("retryAudio() re-narrates the same page once the network is back", async () => {
    engine = failingEngine(1); // fail once, then recover
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    expect(store.state.audioError).toBe(true);

    store.retryAudio(); // the bird was tapped
    await flush();
    expect(store.state.audioError).toBe(false);
    expect(narrations()).toEqual(["/s/p1.wav", "/s/p1.wav"]);
  });

  it("while the bird holds the stage, sync neither narrates nor pauses", async () => {
    engine = failingEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    const callsWhenErrored = engine.calls.length;

    store.togglePlay(); // stray taps land under the overlay
    store.togglePlay();
    expect(engine.calls.length).toBe(callsWhenErrored);
  });

  it("a failure that lands after the child left the player never wakes the bird", async () => {
    const wrapped = fakeEngine();
    let rejectLate;
    const playNarration = wrapped.playNarration;
    let firstCall = true;
    wrapped.playNarration = async (url, opts) => {
      if (firstCall) {
        firstCall = false;
        return new Promise((_, reject) => {
          rejectLate = reject; // page 1's voice hangs on the wire
        });
      }
      return playNarration(url, opts);
    };
    engine = wrapped;
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt();

    store.exitStory(); // the child bails out to the shelf...
    rejectLate(new Error("late failure"));
    await flush();

    // ...so the stale failure is noise: no bird, no retry prompt.
    expect(store.state.audioError).toBe(false);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);
  });

  it("a missing audio_retry prompt still shows the bird, just silently", async () => {
    engine = failingEngine();
    playback = createPlayback({
      store,
      engine,
      prefetcher,
      prompts: { story_start: PROMPTS.story_start, end: PROMPTS.end },
    });
    await playback.openStory(fixtureStory());
    engine.endPrompt();
    await flush();
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);
  });
});
