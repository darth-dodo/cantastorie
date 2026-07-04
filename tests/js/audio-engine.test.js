import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAudioEngine } from "../../static/js/audio-engine.js";

// A fake Web Audio context that records what the engine asks of it.
// jsdom has no AudioContext; the engine takes injected factories precisely
// so its behavior — offsets, ramps, overlap rules — is testable.
function fakeContext() {
  const ctx = {
    currentTime: 0,
    state: "suspended",
    destination: { name: "destination" },
    resume: vi.fn(async () => {
      ctx.state = "running";
    }),
    decodeAudioData: vi.fn(async () => ({ duration: 10 })),
    createGain: () => {
      const gain = {
        gain: {
          value: 1,
          setValueAtTime: vi.fn((v) => {
            gain.gain.value = v;
          }),
          linearRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      };
      return gain;
    },
    createBufferSource: () => {
      const source = {
        buffer: null,
        onended: null,
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      };
      ctx.sources.push(source);
      return source;
    },
    sources: [],
  };
  return ctx;
}

const okFetch = vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }));

function makeEngine(ctx) {
  return createAudioEngine({ createContext: () => ctx, fetchFn: okFetch, crossfadeSeconds: 0.9 });
}

let ctx;
let engine;

beforeEach(() => {
  okFetch.mockClear();
  ctx = fakeContext();
  engine = makeEngine(ctx);
});

describe("unlock", () => {
  it("resumes a suspended context once, from a gesture", async () => {
    expect(engine.unlocked).toBe(false);
    await engine.unlock();
    expect(ctx.resume).toHaveBeenCalledOnce();
    expect(engine.unlocked).toBe(true);
    await engine.unlock();
    expect(ctx.resume).toHaveBeenCalledOnce(); // idempotent
  });
});

describe("loading", () => {
  it("fetches and decodes each url once, then serves the cache", async () => {
    await engine.load("p1.mp3");
    await engine.load("p1.mp3");
    await engine.load("p2.mp3");
    expect(okFetch).toHaveBeenCalledTimes(2);
  });

  it("a failed fetch rejects with the url", async () => {
    const failing = createAudioEngine({
      createContext: () => ctx,
      fetchFn: async () => ({ ok: false, status: 404 }),
    });
    await expect(failing.load("missing.mp3")).rejects.toThrow(/missing\.mp3.*404/);
  });
});

describe("narration: play, pause, exact-position resume", () => {
  it("plays from an offset and reports position as time passes", async () => {
    await engine.playNarration("p1.mp3", { offset: 2 });
    expect(engine.state).toBe("playing");
    expect(ctx.sources[0].start).toHaveBeenCalledWith(0, 2);

    ctx.currentTime = 3.5;
    expect(engine.position()).toBeCloseTo(5.5); // 2 + 3.5
  });

  it("pause captures the exact position; resume restarts the source there", async () => {
    await engine.playNarration("p1.mp3");
    ctx.currentTime = 4.2;

    const offset = engine.pauseNarration();
    expect(offset).toBeCloseTo(4.2);
    expect(engine.state).toBe("paused");
    expect(ctx.sources[0].stop).toHaveBeenCalled();

    await engine.resumeNarration();
    expect(engine.state).toBe("playing");
    expect(ctx.sources[1].start).toHaveBeenCalledWith(0, offset);
  });

  it("a natural end reaches idle and calls onEnded — the page-turn hook", async () => {
    const onEnded = vi.fn();
    await engine.playNarration("p1.mp3", { onEnded });
    ctx.sources[0].onended();
    expect(engine.state).toBe("idle");
    expect(onEnded).toHaveBeenCalledOnce();
  });

  it("a new page crossfades: old voice ramps to silence, new voice ramps in", async () => {
    await engine.playNarration("p1.mp3");
    ctx.currentTime = 5;
    await engine.playNarration("p2.mp3");

    const [oldSource, newSource] = ctx.sources;
    expect(oldSource.stop).toHaveBeenCalledWith(5.9); // now + crossfade
    expect(newSource.start).toHaveBeenCalledWith(0, 0);
    expect(engine.state).toBe("playing");

    // The silenced voice's late onended must not fire END or onEnded
    oldSource.onended?.();
    expect(engine.state).toBe("playing");
  });
});

describe("prompts never overlap narration", () => {
  it("a prompt ducks narration at its exact position and hands the story back", async () => {
    await engine.playNarration("p1.mp3");
    ctx.currentTime = 3;

    await engine.playPrompt("well-chosen.mp3");
    expect(engine.state).toBe("ducked");
    expect(ctx.sources[0].stop).toHaveBeenCalled(); // narration silenced

    ctx.currentTime = 4;
    await ctx.sources[1].onended(); // prompt finishes
    expect(engine.state).toBe("playing");
    expect(ctx.sources[2].start).toHaveBeenCalledWith(0, 3); // resumed where held
  });

  it("two prompts never overlap: the second silences the first", async () => {
    await engine.playPrompt("one.mp3");
    await engine.playPrompt("two.mp3");
    expect(ctx.sources[0].stop).toHaveBeenCalled();
    expect(ctx.sources[1].start).toHaveBeenCalled();
  });

  it("pausing during a duck sticks: the prompt's end does not un-pause", async () => {
    await engine.playNarration("p1.mp3");
    ctx.currentTime = 3;
    await engine.playPrompt("nudge.mp3");

    engine.pauseNarration();
    expect(engine.state).toBe("paused");

    await ctx.sources[1].onended();
    expect(engine.state).toBe("paused"); // still paused
    expect(ctx.sources).toHaveLength(2); // no new narration voice started
  });

  it("prompt onEnded fires for chaining spoken sequences", async () => {
    const onEnded = vi.fn();
    await engine.playPrompt("greeting.mp3", { onEnded });
    await ctx.sources[0].onended();
    expect(onEnded).toHaveBeenCalledOnce();
  });
});

describe("stopAll", () => {
  it("silences every voice and returns to idle", async () => {
    await engine.playNarration("p1.mp3");
    await engine.playPrompt("nudge.mp3");
    engine.stopAll();
    expect(engine.state).toBe("idle");
    ctx.sources.forEach((s) => expect(s.stop).toHaveBeenCalled());
    expect(engine.position()).toBe(0);
  });
});
