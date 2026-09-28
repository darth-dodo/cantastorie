import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createPlayback,
  STALL_POLL_MS,
  STALL_TIMEOUT_MS,
  STORY_START_LOAD_TIMEOUT_MS,
} from "../../src/static/js/playback.js";
import { createStore } from "../../src/static/js/store.js";
import { createAudioEngine } from "../../src/static/js/audio-engine.js";

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
  let position = 0; // seconds into the live voice; the tests move it by hand
  return {
    calls,
    get state() {
      return state;
    },
    // The real engine's getter: the context is running. The cover tap has
    // unlocked it by the time a story plays; a stall test may flip it.
    unlocked: true,
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
      if (state === "playing") state = "ducked"; // narration dips under the prompt
    },
    // The real engine derives this from ctx.currentTime, which freezes
    // while the context is suspended; here the test drives it.
    position() {
      return position;
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
      if (state === "ducked") state = "playing";
      finished?.onEnded?.();
    },
    // Test driver: the audio clock's reading. Leaving it alone freezes the voice.
    setPosition(seconds) {
      position = seconds;
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
let hidden; // the tab's visibility, as the watchdog reads it
const isHidden = () => hidden;

beforeEach(() => {
  hidden = false;
  store = createStore();
  engine = fakeEngine();
  prefetcher = { prefetchStory: vi.fn(async () => ({ total: 16, loaded: 16, failed: 0 })) };
  playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS, isHidden });
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

describe("Branch following — the tapped option extends the played path (AI-428)", () => {
  // A branching fixture: p1 -> p2 -> p3 (choice: arm A begins at a1, arm B
  // at b1). Each arm is two linear pages. Mirrors loadStory's playable shape
  // (id/next_page/choice ride along) and its pagesFrom walk.
  function branchingStory() {
    const page = (id, next_page, choice = null) => ({
      id,
      text: id,
      audioUrl: `/s/${id}.wav`,
      imageUrl: `/s/${id}.webp`,
      next_page,
      choice,
    });
    const allPages = [
      page("p1", "p2"),
      page("p2", "p3"),
      page("p3", null, { options: [{ next_page: "a1" }, { next_page: "b1" }] }),
      page("a1", "a2"),
      page("a2", null),
      page("b1", "b2"),
      page("b2", null),
    ];
    const byId = new Map(allPages.map((p) => [p.id, p]));
    const pagesFrom = (pageId) => {
      const ordered = [];
      const seen = new Set();
      let current = byId.get(pageId);
      while (current && !seen.has(current.id)) {
        ordered.push(current);
        seen.add(current.id);
        current = current.next_page ? byId.get(current.next_page) : null;
      }
      return ordered;
    };
    // The heard path halts at the choice page (p3.next_page is null).
    return { id: "branchy", title: "Branchy", pages: pagesFrom("p1"), allPages, pagesFrom };
  }

  it("extendPath appends the arm and narration turns into it", async () => {
    const loaded = branchingStory();
    await playback.openStory(loaded);
    engine.endPrompt();

    engine.endNarration(); // p1 -> p2
    engine.endNarration(); // p2 -> p3 (the choice page)
    engine.endNarration(); // p3 audio ends -> the choice opens, no turn
    expect(store.state).toMatchObject({ page: 2, choiceOpen: true });

    // The main.js wiring order: extend the played path, THEN choose. choose()
    // advances page to choicePage + 1, which must already be the arm's first page.
    playback.extendPath(loaded.pagesFrom("a1"));
    store.choose(0);

    // The next narrated page is the arm's first page, and it carries on.
    expect(narrations().at(-1)).toBe("/s/a1.wav");
    engine.endNarration();
    expect(narrations().at(-1)).toBe("/s/a2.wav");
  });

  it("extending recomputes the next choicePage for a later branch", async () => {
    const loaded = branchingStory();
    // Make arm A itself branch again: a2 -> choice.
    const a2 = loaded.allPages.find((p) => p.id === "a2");
    a2.next_page = null;
    a2.choice = { options: [{ next_page: "b1" }, { next_page: "b2" }] };
    await playback.openStory(loaded);
    engine.endPrompt();

    engine.endNarration(); // p1 -> p2
    engine.endNarration(); // p2 -> p3
    engine.endNarration(); // p3 -> choice opens
    playback.extendPath(loaded.pagesFrom("a1"));
    store.choose(0); // narrates a1 (index 3)
    expect(store.state.choicePage).toBe(4); // a2 is now the next choice page

    engine.endNarration(); // a1 -> a2 (the new choice page narrates)
    engine.endNarration(); // a2 audio ends -> the choice opens again, no turn
    expect(store.state).toMatchObject({ page: 4, choiceOpen: true });
  });
});

describe("Spoken option labels (AI-428) — the overlay speaks label 0 then label 1", () => {
  // A branching fixture whose choice options carry label audio URLs.
  function branchingStoryWithLabels() {
    const page = (id, next_page, choice = null) => ({
      id,
      text: id,
      audioUrl: `/s/${id}.wav`,
      imageUrl: `/s/${id}.webp`,
      next_page,
      choice,
    });
    const allPages = [
      page("p1", "p2"),
      page("p2", "p3"),
      page("p3", null, {
        options: [
          { label: "a", card_image: "/s/opt0.webp", audioUrl: "/s/opt0.wav", next_page: "a1" },
          { label: "b", card_image: "/s/opt1.webp", audioUrl: "/s/opt1.wav", next_page: "b1" },
        ],
      }),
      page("a1", null),
      page("b1", null),
    ];
    const byId = new Map(allPages.map((p) => [p.id, p]));
    const pagesFrom = (pageId) => {
      const ordered = [];
      const seen = new Set();
      let current = byId.get(pageId);
      while (current && !seen.has(current.id)) {
        ordered.push(current);
        seen.add(current.id);
        current = current.next_page ? byId.get(current.next_page) : null;
      }
      return ordered;
    };
    return { id: "labels", title: "Labels", pages: pagesFrom("p1"), allPages, pagesFrom };
  }

  // The labels spoken on the prompt channel, excluding the story-start prompt
  // that always fires on the cover tap.
  function labelsSpoken() {
    return promptsSpoken().filter((url) => url !== PROMPTS.story_start);
  }

  async function openToChoice(story) {
    await playback.openStory(story);
    engine.endPrompt(); // the story-start prompt ends; page 1 narrates
    engine.endNarration(); // p1 -> p2
    engine.endNarration(); // p2 -> p3 (choice page)
    engine.endNarration(); // p3 audio ends -> the choice opens
  }

  it("opening the overlay speaks label 0, then label 1 only after 0 ends", async () => {
    await openToChoice(branchingStoryWithLabels());
    expect(store.state.choiceOpen).toBe(true);

    // Label 0 speaks on the prompt channel; label 1 waits its turn.
    expect(labelsSpoken()).toEqual(["/s/opt0.wav"]);

    // ...and only once label 0 finishes does label 1 speak.
    engine.endPrompt();
    expect(labelsSpoken()).toEqual(["/s/opt0.wav", "/s/opt1.wav"]);
  });

  it("labels never overlap the narration — they wait until the choice-page audio ended", async () => {
    const story = branchingStoryWithLabels();
    await playback.openStory(story);
    engine.endPrompt();
    engine.endNarration(); // p1 -> p2
    engine.endNarration(); // p2 -> p3 (choice page narrates)

    // The choice page is still narrating; no label has spoken yet.
    expect(labelsSpoken()).toEqual([]);

    engine.endNarration(); // p3 audio ends -> overlay opens, labels begin
    expect(labelsSpoken()).toEqual(["/s/opt0.wav"]);
  });

  it("options without audioUrl skip silently (the dev fixture)", async () => {
    const story = branchingStoryWithLabels();
    story.allPages.find((p) => p.id === "p3").choice.options.forEach((o) => {
      o.audioUrl = null;
    });
    await openToChoice(story);
    expect(store.state.choiceOpen).toBe(true);
    expect(labelsSpoken()).toEqual([]);
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

  it("a load failure while the context is locked plays no prompt; while unlocked it plays once", async () => {
    engine = failingEngine();
    engine.unlocked = false; // a prompt started now would sit frozen until the retry tap
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt(); // "Si parte!" ends; page 1's voice rejects
    await flush();
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);

    engine.unlocked = true; // the bird's tap wakes the context; the wire is still dead
    store.retryAudio();
    await flush();
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken().filter((url) => url === PROMPTS.audio_retry)).toHaveLength(1);
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

  it("retryAudio() that fails again leaves the player in audioError — no page skip, no wedge", async () => {
    engine = failingEngine(); // all narration calls keep failing
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS });
    await playback.openStory(fixtureStory());
    engine.endPrompt(); // "Si parte!" ends; page 1's voice rejects (first failure)
    await flush();
    expect(store.state.audioError).toBe(true);

    const pageBeforeRetry = store.state.page;

    store.retryAudio(); // the bird was tapped; narration is attempted again and fails
    await flush();

    // The player must be back in audioError — not silently advanced to the next page.
    expect(store.state.audioError).toBe(true);
    // No page skip happened.
    expect(store.state.page).toBe(pageBeforeRetry);
    // The player is still in the player screen, not wedged on end/shelf.
    expect(store.state.screen).toBe("player");
  });
});

describe("Stall watchdog (B9) — a frozen voice hands the stage to the sleeping bird", () => {
  // A suspended audio context freezes ctx.currentTime without any error:
  // the story looks like it is playing but nothing is heard. The watchdog
  // notices the position standing still and wakes the bird instead.
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    store.toShelf(); // leave the player so the watchdog stops
    expect(vi.getTimerCount()).toBe(0); // nothing leaks out of a test
    vi.useRealTimers();
  });

  async function openAndNarrate() {
    await openFresh();
    engine.endPrompt(); // "Si parte!" ends; page 1 narrates
    expect(engine.state).toBe("playing");
  }

  it("a voice whose position keeps advancing never wakes the bird", async () => {
    await openAndNarrate();
    let seconds = 0;
    for (let elapsed = 0; elapsed < 5000; elapsed += STALL_POLL_MS) {
      seconds += STALL_POLL_MS / 1000;
      engine.setPosition(seconds);
      vi.advanceTimersByTime(STALL_POLL_MS);
    }
    expect(store.state.audioError).toBe(false);
  });

  it("a position frozen for STALL_TIMEOUT_MS holds the voice and wakes the bird once, silently", async () => {
    await openAndNarrate();
    const pause = vi.spyOn(engine, "pauseNarration");
    const audioError = vi.spyOn(store, "audioError");

    vi.advanceTimersByTime(STALL_TIMEOUT_MS - STALL_POLL_MS);
    expect(store.state.audioError).toBe(false); // not yet: the clock is still counting

    vi.advanceTimersByTime(STALL_POLL_MS);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(audioError).toHaveBeenCalledTimes(1);
    // The exact spot is held before the bird takes the stage.
    expect(pause.mock.invocationCallOrder[0]).toBeLessThan(audioError.mock.invocationCallOrder[0]);
    expect(store.state.audioError).toBe(true);
    expect(engine.state).toBe("paused");
    expect(promptsSpoken()).toEqual([PROMPTS.story_start]); // a stall never speaks

    // Still frozen: the bird already holds the stage, nothing fires again.
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(audioError).toHaveBeenCalledTimes(1);
  });

  it("the retry tap after a stall resumes the held voice mid-sentence — no restart of the page", async () => {
    await openAndNarrate();
    vi.advanceTimersByTime(STALL_TIMEOUT_MS);
    expect(store.state.audioError).toBe(true);

    store.retryAudio(); // the bird was tapped
    await vi.advanceTimersByTimeAsync(0);

    expect(engine.calls).toContainEqual(["resume", "/s/p1.wav"]);
    expect(narrations()).toEqual(["/s/p1.wav"]);
    expect(engine.state).toBe("playing");
  });

  it("a paused story never wakes the bird", async () => {
    await openAndNarrate();
    store.togglePlay();
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(store.state.audioError).toBe(false);
  });

  it("a voice ducked under a prompt never wakes the bird", async () => {
    await openAndNarrate();
    engine.playPrompt("/p/aside.wav");
    expect(engine.state).toBe("ducked");
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(store.state.audioError).toBe(false);
  });

  it("the choice overlay never wakes the bird", async () => {
    const story = fixtureStory();
    story.pages[0].choice = { options: [{ next_page: "p2" }, { next_page: "p2" }] };
    await playback.openStory(story);
    engine.endPrompt();
    engine.endNarration(); // p1 is the choice page: its audio end opens the overlay
    expect(store.state.choiceOpen).toBe(true);
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(store.state.audioError).toBe(false);
  });

  it("the resume overlay never wakes the bird", async () => {
    store = createStore({ page: 3 });
    engine = fakeEngine();
    playback = createPlayback({ store, engine, prefetcher, prompts: PROMPTS, isHidden });
    await playback.openStory(fixtureStory());
    expect(store.state.resumeOpen).toBe(true);
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(store.state.audioError).toBe(false);
  });

  it("off the player screen the watchdog never fires", async () => {
    await openAndNarrate();
    const pause = vi.spyOn(engine, "pauseNarration");
    store.toShelf();
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(pause).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("the start prompt speaking is not a stall", async () => {
    await openFresh(); // "Si parte!" is still speaking; page 1 waits
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(store.state.audioError).toBe(false);
  });

  it("a page turn resets the stall clock", async () => {
    await openAndNarrate();
    vi.advanceTimersByTime(2000); // frozen, but not long enough
    engine.endNarration(); // the page turns; page 2 narrates
    expect(store.state.page).toBe(1);
    vi.advanceTimersByTime(2000); // frozen again: 4 s in all, 2 s on this page
    expect(store.state.audioError).toBe(false);
  });

  it("the interval is cleared on the bird waking and on leaving the player — none leak", async () => {
    // A fresh store and engine, so the default instance above stays out of it.
    store = createStore();
    engine = fakeEngine();
    const live = new Map(); // injected id -> the real timer behind it
    let nextId = 1;
    const setIntervalFn = vi.fn((fn, ms) => {
      const id = nextId++;
      live.set(id, setInterval(fn, ms));
      return id;
    });
    const clearIntervalFn = vi.fn((id) => {
      clearInterval(live.get(id));
      live.delete(id);
    });
    playback = createPlayback({
      store,
      engine,
      prefetcher,
      prompts: PROMPTS,
      setIntervalFn,
      clearIntervalFn,
      isHidden,
    });
    await openAndNarrate();
    expect(setIntervalFn).toHaveBeenCalledWith(expect.any(Function), STALL_POLL_MS);
    expect(live.size).toBe(1);

    // The bird waking stops the poll...
    vi.advanceTimersByTime(STALL_TIMEOUT_MS);
    expect(store.state.audioError).toBe(true);
    expect(live.size).toBe(0);

    // ...a retry starts it again, and leaving the player stops it.
    store.retryAudio();
    await vi.advanceTimersByTimeAsync(0);
    expect(live.size).toBe(1);
    store.toShelf();
    expect(live.size).toBe(0);
    expect(clearIntervalFn).toHaveBeenCalledTimes(setIntervalFn.mock.calls.length);
  });

  it("a manual page tap mid-voice (no audio end between) resets the stall clock", async () => {
    await openAndNarrate();
    vi.advanceTimersByTime(2000); // page 1 frozen, but not long enough
    store.nextPage(); // the child taps next: page 2 narrates over page 1, no END
    expect(narrations()).toEqual(["/s/p1.wav", "/s/p2.wav"]);
    vi.advanceTimersByTime(2000); // 4 s frozen in all, 2 s on this page
    expect(store.state.audioError).toBe(false);
  });

  it("a stall while the context is locked wakes the bird silently — no prompt queued to play over the story", async () => {
    await openAndNarrate();
    engine.unlocked = false; // the suspended context behind the stall
    vi.advanceTimersByTime(STALL_TIMEOUT_MS);
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).toEqual([PROMPTS.story_start]);

    // The retry tap wakes the context and the story resumes alone.
    engine.unlocked = true;
    store.retryAudio();
    await vi.advanceTimersByTimeAsync(0);
    expect(engine.calls).toContainEqual(["resume", "/s/p1.wav"]);
    expect(promptsSpoken()).toEqual([PROMPTS.story_start]);
  });

  it("a stall never speaks the retry line, even while the context reads unlocked", async () => {
    // The frozen clock is the stall: a prompt started now could only play
    // late, over the resumed story, whatever the context claims.
    await openAndNarrate();
    expect(engine.unlocked).toBe(true);
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 3);
    expect(store.state.audioError).toBe(true);
    expect(promptsSpoken()).not.toContain(PROMPTS.audio_retry);
  });

  describe("a hidden tab — the wake on return gets its chance first", () => {
    it("hidden and frozen for more than 5 s never wakes the bird", async () => {
      await openAndNarrate();
      hidden = true;
      vi.advanceTimersByTime(6000);
      expect(store.state.audioError).toBe(false);
      expect(engine.state).toBe("playing");
    });

    it("back to visible with the position advancing never wakes the bird", async () => {
      await openAndNarrate();
      hidden = true;
      vi.advanceTimersByTime(6000);
      hidden = false; // the wake on return got the voice moving again
      let seconds = 0;
      for (let elapsed = 0; elapsed < 5000; elapsed += STALL_POLL_MS) {
        seconds += STALL_POLL_MS / 1000;
        engine.setPosition(seconds);
        vi.advanceTimersByTime(STALL_POLL_MS);
      }
      expect(store.state.audioError).toBe(false);
    });

    it("back to visible but still frozen for STALL_TIMEOUT_MS wakes the bird", async () => {
      await openAndNarrate();
      hidden = true;
      vi.advanceTimersByTime(6000);
      hidden = false;
      // The clock starts over on return: the first visible poll is its
      // first sample, so the whole STALL_TIMEOUT_MS is counted while visible.
      vi.advanceTimersByTime(STALL_TIMEOUT_MS);
      expect(store.state.audioError).toBe(false);
      vi.advanceTimersByTime(STALL_POLL_MS);
      expect(store.state.audioError).toBe(true);
    });
  });

  it("an instance left behind by clearStory never fires, even when the shared engine stalls", async () => {
    await openAndNarrate();
    const pause = vi.spyOn(engine, "pauseNarration");
    playback.clearStory(); // the language switch retires this instance
    vi.advanceTimersByTime(STALL_TIMEOUT_MS * 2);
    expect(pause).not.toHaveBeenCalled();
    expect(store.state.audioError).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("A narration fetch that never answers (B8 review, AI-473)", () => {
  // The real engine over a minimal fake context, so the time-to-headers
  // timeout in engine.load() is what ends the wait, not a test double.
  function minimalContext() {
    return {
      currentTime: 0,
      state: "running",
      destination: {},
      resume: async () => {},
      decodeAudioData: async () => ({ duration: 10 }),
      createGain: () => ({
        gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} },
        connect() {},
      }),
      createBufferSource: () => ({ buffer: null, onended: null, connect() {}, start() {}, stop() {} }),
    };
  }

  it("a hung page-1 fetch wakes the bird, and the retry tap refetches and narrates", async () => {
    let up = false;
    const narrationFetches = [];
    const fetchFn = (url, { signal } = {}) => {
      narrationFetches.push(url);
      if (up) return Promise.resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });
      return new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason ?? new Error("aborted")));
      });
    };
    const realEngine = createAudioEngine({
      createContext: minimalContext,
      fetchFn,
      audioLoadTimeoutMs: 30,
    });
    playback = createPlayback({ store, engine: realEngine, prefetcher: null, prompts: {} });
    await playback.openStory(fixtureStory());

    // Without a timeout the load never settles: no voice, no bird, silence.
    await vi.waitFor(() => expect(store.state.audioError).toBe(true));
    expect(realEngine.state).not.toBe("playing");

    up = true;
    store.retryAudio(); // the bird was tapped
    await vi.waitFor(() => expect(realEngine.state).toBe("playing"));
    expect(store.state.audioError).toBe(false);
    expect(narrationFetches.filter((url) => url === "/s/p1.wav")).toHaveLength(2);
    playback.clearStory();
  });
});
