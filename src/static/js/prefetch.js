// Whole-story prefetch (docs/architecture.md → The Player). On cover tap
// the player banks every page's audio and image — both branch options
// before each choice point, because children tap instantly. Audio goes
// through the audio engine (decoded buffers are the bank); images warm
// the browser's HTTP cache. A missed asset is counted, never fatal:
// the audio-retry and offline states (slice 2) cover the truly bad night.
//
// Budget tablets (M19, M21): page 1 loads alone first, so its narration
// never shares the line, then the rest follow in page order through a small
// pool. Opening a story keeps only its own audio and the spoken prompts
// decoded; every other story's buffers are dropped.

export const PREFETCH_CONCURRENCY = 3;

export function createPrefetcher({
  engine,
  fetchFn = (...args) => globalThis.fetch(...args),
  // Prompt audio that stays decoded across story switches (the shelf
  // greeting, the offline line), beside the prompts each story passes in.
  keepAudio = [],
}) {
  const requested = new Set();
  const bankedAudio = new Set(); // audio URLs the engine was asked to decode
  let total = 0;
  let loaded = 0;
  let failed = 0;
  let generation = 0; // bumped per prefetch; an older queue stops starting jobs

  // Plans one asset: counted and marked now (so a second prefetch dedupes),
  // run later by the pool. A job whose prefetch was superseded before it
  // started is unmarked instead, so a later prefetch can still bank it.
  function plan(url, task, jobGeneration) {
    if (!url || requested.has(url)) return null;
    requested.add(url);
    total += 1;
    return async () => {
      if (jobGeneration !== generation) {
        requested.delete(url);
        bankedAudio.delete(url);
        total -= 1;
        return;
      }
      try {
        await task();
        loaded += 1;
      } catch {
        failed += 1;
      }
    };
  }

  function planAudio(url, jobGeneration) {
    const job = plan(url, () => engine.load(url), jobGeneration);
    if (job) bankedAudio.add(url);
    return job;
  }

  async function warmImage(url) {
    const res = await fetchFn(url);
    if (!res.ok) throw new Error(`image fetch failed: ${url} (${res.status})`);
    await res.arrayBuffer?.();
  }

  function pageJobs(page, jobGeneration) {
    const jobs = [
      planAudio(page.audioUrl, jobGeneration),
      plan(page.imageUrl, () => warmImage(page.imageUrl), jobGeneration),
    ];
    // A choice page's option cards and spoken labels bank like page
    // assets: card images warm the HTTP cache, label audio decodes into
    // the engine. A miss is counted, never fatal, same as any page asset.
    for (const option of page.choice?.options ?? []) {
      jobs.push(plan(option.card_image, () => warmImage(option.card_image), jobGeneration));
      jobs.push(planAudio(option.audioUrl, jobGeneration));
    }
    return jobs.filter(Boolean);
  }

  function storyAudio(pages) {
    const urls = [];
    for (const page of pages) {
      urls.push(page.audioUrl);
      for (const option of page.choice?.options ?? []) urls.push(option.audioUrl);
    }
    return urls.filter(Boolean);
  }

  // Drops every decoded buffer outside this story and the prompts, and
  // forgets having banked them, so coming back to a story banks it again.
  function evictOthers(keep) {
    engine.retainOnly?.([...keep]);
    for (const url of bankedAudio) {
      if (keep.has(url)) continue;
      bankedAudio.delete(url);
      requested.delete(url);
    }
  }

  async function runPool(jobs, limit) {
    let next = 0;
    const worker = async () => {
      while (next < jobs.length) {
        const job = jobs[next];
        next += 1;
        await job();
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, jobs.length) }, worker));
  }

  return {
    status: () => ({ total, loaded, failed }),

    // Resolves when every not-yet-banked asset settles; safe to fire and
    // forget — playback only ever waits on the engine's own load().
    // extraAudio carries the story's spoken prompts (start, end): the end
    // prompt must be local long before the end screen asks for it.
    async prefetchStory(story, extraAudio = []) {
      generation += 1;
      const jobGeneration = generation;
      const pages = story.allPages ?? story.pages;
      evictOthers(new Set([...storyAudio(pages), ...extraAudio, ...keepAudio]));

      // Page 1 is the page the child hears first: it loads alone.
      const first = story.pages?.[0] ?? pages[0];
      const firstJobs = first ? pageJobs(first, jobGeneration) : [];
      const laterJobs = extraAudio.map((url) => planAudio(url, jobGeneration)).filter(Boolean);
      for (const page of pages) {
        if (page === first) continue;
        laterJobs.push(...pageJobs(page, jobGeneration));
      }

      await runPool(firstJobs, PREFETCH_CONCURRENCY);
      await runPool(laterJobs, PREFETCH_CONCURRENCY);
      return { total, loaded, failed };
    },
  };
}
