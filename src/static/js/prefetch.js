// Whole-story prefetch (docs/architecture.md → The Player). On cover tap
// the player banks every page's audio and image — both branch options
// before each choice point, because children tap instantly. Audio goes
// through the audio engine (decoded buffers are the bank); images warm
// the browser's HTTP cache. A missed asset is counted, never fatal:
// the audio-retry and offline states (slice 2) cover the truly bad night.

export function createPrefetcher({ engine, fetchFn = (...args) => globalThis.fetch(...args) }) {
  const requested = new Set();
  let total = 0;
  let loaded = 0;
  let failed = 0;

  function bank(url, task) {
    if (!url || requested.has(url)) return null;
    requested.add(url);
    total += 1;
    return Promise.resolve()
      .then(task)
      .then(() => {
        loaded += 1;
      })
      .catch(() => {
        failed += 1;
      });
  }

  async function warmImage(url) {
    const res = await fetchFn(url);
    if (!res.ok) throw new Error(`image fetch failed: ${url} (${res.status})`);
    await res.arrayBuffer?.();
  }

  return {
    status: () => ({ total, loaded, failed }),

    // Resolves when every not-yet-banked asset settles; safe to fire and
    // forget — playback only ever waits on the engine's own load().
    async prefetchStory(story) {
      const pages = story.allPages ?? story.pages;
      const jobs = [];
      for (const page of pages) {
        jobs.push(bank(page.audioUrl, () => engine.load(page.audioUrl)));
        jobs.push(bank(page.imageUrl, () => warmImage(page.imageUrl)));
      }
      await Promise.all(jobs.filter(Boolean));
      return { total, loaded, failed };
    },
  };
}
