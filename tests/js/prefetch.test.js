import { describe, expect, it, vi } from "vitest";
import { createAudioEngine } from "../../src/static/js/audio-engine.js";
import { PREFETCH_CONCURRENCY, createPrefetcher } from "../../src/static/js/prefetch.js";

// Whole-story prefetch (architecture.md -> The Player): on cover tap the
// player banks every page's audio and image, so mid-story network failures
// become nearly impossible. Audio goes through the audio engine — its
// decoded-buffer cache is the bank — and images warm the HTTP cache.

function storyOfPages(count) {
  return {
    pages: Array.from({ length: count }, (_, i) => ({
      id: `p${i + 1}`,
      audioUrl: `/s/p${i + 1}.wav`,
      imageUrl: `/s/p${i + 1}.webp`,
    })),
  };
}

function fakeEngine() {
  return { load: vi.fn(async (url) => ({ url })) };
}

const okFetch = () => vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) }));

describe("Whole-story prefetch", () => {
  it("fetches every page's audio through the audio engine and every image once", async () => {
    // Given an 8-page story, when the cover is tapped...
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });

    await prefetcher.prefetchStory(storyOfPages(8));

    // ...then all 8 audio files decode into the engine and all 8 images warm the cache.
    expect(engine.load).toHaveBeenCalledTimes(8);
    expect(fetchFn).toHaveBeenCalledTimes(8);
    expect(prefetcher.status()).toEqual({ total: 16, loaded: 16, failed: 0 });
  });

  it("keeps its bookkeeping across replays: a second prefetch refetches nothing", async () => {
    // Given the story was already banked on the first cover tap...
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });
    const story = storyOfPages(8);
    await prefetcher.prefetchStory(story);

    // ...when the child replays ("Ancora!") and the story opens again...
    await prefetcher.prefetchStory(story);

    // ...then no request leaves twice.
    expect(engine.load).toHaveBeenCalledTimes(8);
    expect(fetchFn).toHaveBeenCalledTimes(8);
    expect(prefetcher.status().total).toBe(16);
  });

  it("banks both branch options before a choice: allPages, not just the heard path", async () => {
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });

    const branching = {
      pages: [{ id: "p1", audioUrl: "/s/p1.wav", imageUrl: "/s/p1.webp" }],
      allPages: [
        { id: "p1", audioUrl: "/s/p1.wav", imageUrl: "/s/p1.webp" },
        { id: "p2a", audioUrl: "/s/p2a.wav", imageUrl: "/s/p2a.webp" },
        { id: "p2b", audioUrl: "/s/p2b.wav", imageUrl: "/s/p2b.webp" },
      ],
    };
    await prefetcher.prefetchStory(branching);

    expect(engine.load).toHaveBeenCalledTimes(3);
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });

  it("banks each choice option's card image and label audio alongside page assets", async () => {
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });

    const branching = {
      pages: [{ id: "p1", audioUrl: "/s/p1.wav", imageUrl: "/s/p1.webp" }],
      allPages: [
        { id: "p1", audioUrl: "/s/p1.wav", imageUrl: "/s/p1.webp" },
        {
          id: "p2",
          audioUrl: "/s/p2.wav",
          imageUrl: "/s/p2.webp",
          choice: {
            options: [
              { card_image: "/s/opt0.webp", audioUrl: "/s/opt0.wav" },
              { card_image: "/s/opt1.webp", audioUrl: "/s/opt1.wav" },
            ],
          },
        },
      ],
    };
    await prefetcher.prefetchStory(branching);

    // Page audio p1+p2 and both label audios decode into the engine (4).
    expect(engine.load).toHaveBeenCalledWith("/s/opt0.wav");
    expect(engine.load).toHaveBeenCalledWith("/s/opt1.wav");
    expect(engine.load).toHaveBeenCalledTimes(4);
    // Page images p1+p2 and both card images warm the HTTP cache (4).
    expect(fetchFn).toHaveBeenCalledWith("/s/opt0.webp");
    expect(fetchFn).toHaveBeenCalledWith("/s/opt1.webp");
    expect(fetchFn).toHaveBeenCalledTimes(4);
    expect(prefetcher.status()).toEqual({ total: 8, loaded: 8, failed: 0 });
  });

  it("a failed card image is counted, never fatal — the rest still bank", async () => {
    const engine = fakeEngine();
    const fetchFn = vi.fn(async (url) =>
      url === "/s/opt1.webp"
        ? { ok: false, status: 404 }
        : { ok: true, arrayBuffer: async () => new ArrayBuffer(1) },
    );
    const prefetcher = createPrefetcher({ engine, fetchFn });

    const branching = {
      pages: [],
      allPages: [
        {
          id: "p1",
          audioUrl: "/s/p1.wav",
          imageUrl: "/s/p1.webp",
          choice: {
            options: [
              { card_image: "/s/opt0.webp", audioUrl: "/s/opt0.wav" },
              { card_image: "/s/opt1.webp", audioUrl: "/s/opt1.wav" },
            ],
          },
        },
      ],
    };
    // Nothing throws; the one bad card is counted, the rest bank.
    await prefetcher.prefetchStory(branching);
    const status = prefetcher.status();
    expect(status.failed).toBe(1);
    expect(status.loaded).toBe(status.total - 1);
  });

  it("a failed asset never sinks the story: the failure is counted, the rest still bank", async () => {
    // Given one image 404s on a bad night...
    const engine = fakeEngine();
    const fetchFn = vi.fn(async (url) =>
      url === "/s/p3.webp"
        ? { ok: false, status: 404 }
        : { ok: true, arrayBuffer: async () => new ArrayBuffer(1) },
    );
    const prefetcher = createPrefetcher({ engine, fetchFn });

    // ...when the story prefetches, nothing throws...
    await prefetcher.prefetchStory(storyOfPages(8));

    // ...and the books show 15 banked, 1 failed.
    expect(prefetcher.status()).toEqual({ total: 16, loaded: 15, failed: 1 });
  });

  it("banks the story's spoken prompts with the pages — the end prompt is local long before the end", async () => {
    // Given the cover tap hands over the start and end prompt URLs...
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });

    await prefetcher.prefetchStory(storyOfPages(8), ["/p/story-start.wav", "/p/end.wav"]);

    // ...then they decode into the engine alongside the page audio.
    expect(engine.load).toHaveBeenCalledWith("/p/story-start.wav");
    expect(engine.load).toHaveBeenCalledWith("/p/end.wav");
    expect(prefetcher.status()).toEqual({ total: 18, loaded: 18, failed: 0 });
  });

  it("pages without assets ask for nothing (a text-only dev story still opens)", async () => {
    const engine = fakeEngine();
    const fetchFn = okFetch();
    const prefetcher = createPrefetcher({ engine, fetchFn });

    await prefetcher.prefetchStory({ pages: [{ id: "p1", audioUrl: null, imageUrl: null }] });

    expect(engine.load).not.toHaveBeenCalled();
    expect(fetchFn).not.toHaveBeenCalled();
    expect(prefetcher.status().total).toBe(0);
  });
});

// Budget tablets (M19, M21, #187): decoded PCM is large, so only the open
// story and the spoken prompts stay decoded; and page 1 must not share its
// bandwidth with twenty parallel requests.
describe("Budget tablets: one story's audio stays decoded (M19)", () => {
  // The real engine over a fake context: the decoded-buffer cache is what
  // holds the memory, so the spec watches which URLs it must fetch again.
  function realEngine() {
    const ctx = {
      state: "running",
      currentTime: 0,
      decodeAudioData: vi.fn(async () => ({ duration: 10 })),
      resume: vi.fn(async () => {}),
    };
    const audioFetch = vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }));
    const engine = createAudioEngine({ createContext: () => ctx, fetchFn: audioFetch });
    return { engine, audioFetch };
  }

  const storyNamed = (name, count = 3) => ({
    id: name,
    pages: Array.from({ length: count }, (_, i) => ({
      id: `${name}${i + 1}`,
      audioUrl: `/${name}/p${i + 1}.wav`,
      imageUrl: null,
    })),
  });
  const audioFetchesOf = (audioFetch, prefix) =>
    audioFetch.mock.calls.map(([url]) => url).filter((url) => url.startsWith(prefix));

  it("opening another story drops the previous story's decoded audio", async () => {
    const { engine, audioFetch } = realEngine();
    const prefetcher = createPrefetcher({ engine, fetchFn: okFetch() });

    await prefetcher.prefetchStory(storyNamed("a"));
    await prefetcher.prefetchStory(storyNamed("b"));
    expect(audioFetchesOf(audioFetch, "/a/")).toHaveLength(3);

    // Story a's buffers are gone: asking for one decodes it afresh...
    await engine.load("/a/p1.wav");
    expect(audioFetchesOf(audioFetch, "/a/")).toHaveLength(4);
    // ...while the open story's buffers are still banked.
    await engine.load("/b/p1.wav");
    expect(audioFetchesOf(audioFetch, "/b/")).toHaveLength(3);
  });

  it("the spoken prompts stay decoded across a story switch", async () => {
    const { engine, audioFetch } = realEngine();
    const prefetcher = createPrefetcher({
      engine,
      fetchFn: okFetch(),
      keepAudio: ["/p/greeting.wav"],
    });
    await engine.load("/p/greeting.wav");

    await prefetcher.prefetchStory(storyNamed("a"), ["/p/end.wav"]);
    await prefetcher.prefetchStory(storyNamed("b"), ["/p/end.wav"]);

    await engine.load("/p/greeting.wav");
    await engine.load("/p/end.wav");
    expect(audioFetchesOf(audioFetch, "/p/")).toEqual(["/p/greeting.wav", "/p/end.wav"]);
  });

  it("coming back to a story banks it again instead of trusting evicted audio", async () => {
    const { engine, audioFetch } = realEngine();
    const prefetcher = createPrefetcher({ engine, fetchFn: okFetch() });

    await prefetcher.prefetchStory(storyNamed("a"));
    await prefetcher.prefetchStory(storyNamed("b"));
    await prefetcher.prefetchStory(storyNamed("a"));

    expect(audioFetchesOf(audioFetch, "/a/")).toHaveLength(6);
  });
});

describe("Budget tablets: page 1 first, then a small pool in page order (M21)", () => {
  // Every asset request is held open until the spec releases it, so the
  // spec can see exactly what is in flight at each moment.
  function gatedAssets() {
    const started = [];
    const pending = new Map();
    let inFlight = 0;
    let maxInFlight = 0;
    const gate = (url) => {
      started.push(url);
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise((resolve) => {
        pending.set(url, () => {
          inFlight -= 1;
          pending.delete(url);
          resolve();
        });
      });
    };
    return {
      started,
      maxInFlight: () => maxInFlight,
      open: () => [...pending.keys()],
      release: (url) => pending.get(url)(),
      releaseAll: () => [...pending.values()].forEach((done) => done()),
      engine: { load: vi.fn((url) => gate(url)) },
      fetchFn: vi.fn(async (url) => {
        await gate(url);
        return { ok: true, arrayBuffer: async () => new ArrayBuffer(1) };
      }),
    };
  }

  const flushAll = async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  };

  it("exports a concurrency cap of 3", () => {
    expect(PREFETCH_CONCURRENCY).toBe(3);
  });

  it("page 1's audio and image load alone before anything else starts", async () => {
    const assets = gatedAssets();
    const prefetcher = createPrefetcher({ engine: assets.engine, fetchFn: assets.fetchFn });

    const done = prefetcher.prefetchStory(storyOfPages(8), ["/p/end.wav"]);
    await flushAll();
    expect(assets.started).toEqual(["/s/p1.wav", "/s/p1.webp"]);

    assets.release("/s/p1.wav");
    await flushAll();
    expect(assets.started).toEqual(["/s/p1.wav", "/s/p1.webp"]); // still waiting on p1's image

    assets.release("/s/p1.webp");
    await flushAll();
    expect(assets.started).toHaveLength(2 + PREFETCH_CONCURRENCY);

    while (assets.started.length < 17) {
      assets.releaseAll();
      await flushAll();
    }
    assets.releaseAll();
    await done;
    expect(prefetcher.status()).toEqual({ total: 17, loaded: 17, failed: 0 });
  });

  it("never has more than 3 requests in flight, and starts them in page order", async () => {
    const assets = gatedAssets();
    const prefetcher = createPrefetcher({ engine: assets.engine, fetchFn: assets.fetchFn });

    const done = prefetcher.prefetchStory(storyOfPages(8), ["/p/end.wav", "/p/audio-retry.wav"]);
    // Release one request at a time, oldest first, until all have run.
    await flushAll();
    while (assets.open().length > 0) {
      assets.release(assets.open()[0]);
      await flushAll();
    }
    await done;

    expect(assets.maxInFlight()).toBeLessThanOrEqual(PREFETCH_CONCURRENCY);
    // Page 1, then the spoken prompts (the retry line must be local on a bad
    // night), then pages 2 to 8, each page's audio before its image.
    const pageOrder = [
      "/s/p1.wav",
      "/s/p1.webp",
      "/p/end.wav",
      "/p/audio-retry.wav",
      ...Array.from({ length: 7 }, (_, i) => [`/s/p${i + 2}.wav`, `/s/p${i + 2}.webp`]).flat(),
    ];
    expect(assets.started).toEqual(pageOrder);
  });

  it("a story switch stops the old story's queue: its unstarted assets never load", async () => {
    const assets = gatedAssets();
    const prefetcher = createPrefetcher({ engine: assets.engine, fetchFn: assets.fetchFn });
    const storyA = storyOfPages(8);
    const storyB = {
      pages: [{ id: "b1", audioUrl: "/b/p1.wav", imageUrl: null }],
    };

    const doneA = prefetcher.prefetchStory(storyA);
    await flushAll();
    const doneB = prefetcher.prefetchStory(storyB);
    for (let i = 0; i < 20; i++) {
      assets.releaseAll();
      await flushAll();
    }
    await Promise.all([doneA, doneB]);

    expect(assets.started).toContain("/b/p1.wav");
    expect(assets.started.filter((url) => url.startsWith("/s/"))).toEqual(["/s/p1.wav", "/s/p1.webp"]);
  });
  it("tapping story B while A loads still prefetches B's spoken prompts (#187)", async () => {
    const assets = gatedAssets();
    const prefetcher = createPrefetcher({ engine: assets.engine, fetchFn: assets.fetchFn });
    const prompts = ["/p/start.wav", "/p/end.wav", "/p/audio-retry.wav", "/p/choice-nudge.wav"];
    const storyB = { pages: [{ id: "b1", audioUrl: "/b/p1.wav", imageUrl: null }] };

    const doneA = prefetcher.prefetchStory(storyOfPages(8), prompts);
    await flushAll();
    const doneB = prefetcher.prefetchStory(storyB, prompts);
    for (let i = 0; i < 20; i++) {
      assets.releaseAll();
      await flushAll();
    }
    await Promise.all([doneA, doneB]);

    for (const url of prompts) expect(assets.started).toContain(url);
    expect(prefetcher.status().failed).toBe(0);
  });

  it("prefetching the same story twice mid-queue still loads every page (#187)", async () => {
    const assets = gatedAssets();
    const prefetcher = createPrefetcher({ engine: assets.engine, fetchFn: assets.fetchFn });
    const story = storyOfPages(4);

    const first = prefetcher.prefetchStory(story);
    await flushAll();
    const second = prefetcher.prefetchStory(story);
    for (let i = 0; i < 20; i++) {
      assets.releaseAll();
      await flushAll();
    }
    await Promise.all([first, second]);

    for (let i = 1; i <= 4; i++) {
      expect(assets.started).toContain(`/s/p${i}.wav`);
      expect(assets.started).toContain(`/s/p${i}.webp`);
    }
    expect(prefetcher.status()).toEqual({ total: 8, loaded: 8, failed: 0 });
  });
});
