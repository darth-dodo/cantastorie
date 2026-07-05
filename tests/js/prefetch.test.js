import { describe, expect, it, vi } from "vitest";
import { createPrefetcher } from "../../src/static/js/prefetch.js";

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
