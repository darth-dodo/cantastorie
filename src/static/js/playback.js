// The playback loop (AI-364). Narration drives the story: the cover tap
// prefetches the whole story and speaks the story start prompt, each
// page's audio end turns the page immediately — well inside the 500 ms
// budget from docs/product.md — and the audio engine's overlapping gain
// ramps make every turn a gentle crossfade. The end screen speaks the
// end prompt; replay starts the loop over from page 1.
//
// Choice pages are respected but not owned here: audio end on one opens
// the store's overlay and playback simply waits (the overlay, nudge, and
// auto-continue are AI-370).

export function createPlayback({ store, engine, prefetcher = null, prompts = {} }) {
  let story = null;
  let narratingPage = null; // page index whose voice is live or held
  let starting = false; // the story start prompt is speaking; hold page 1
  let prevScreen = store.state.screen;

  function narrate(index) {
    const page = story?.pages[index];
    if (!page?.audioUrl) return;
    narratingPage = index;
    engine
      .playNarration(page.audioUrl, {
        onEnded: () => {
          if (narratingPage !== index) return; // a stale voice never turns the page
          narratingPage = null;
          store.advance(); // Auto page turn: immediate on audio end
        },
      })
      .catch((err) => console.warn("narration failed", err)); // audio-retry state is slice 2
  }

  function sync(state) {
    if (state.screen !== "player") {
      const entering = state.screen !== prevScreen;
      prevScreen = state.screen;
      if (!entering) return;
      narratingPage = null;
      if (state.screen === "end") {
        // The final voice ended naturally; the end prompt speaks over the
        // final scene. Never a stop — nothing snaps at bedtime.
        if (prompts.end) engine.playPrompt(prompts.end).catch(() => {});
      } else {
        engine.stopAll();
      }
      return;
    }
    prevScreen = state.screen;

    if (!story || starting) return;
    if (state.resumeOpen || state.choiceOpen) return;

    if (!state.playing) {
      if (engine.state === "playing" || engine.state === "ducked") engine.pauseNarration();
      return;
    }

    if (narratingPage === state.page) {
      // Same page, back to playing: resume the held voice at its exact position.
      if (engine.state === "paused") {
        engine.resumeNarration().catch((err) => console.warn("resume failed", err));
      }
    } else {
      // A new page (turn, replay, restart): the engine crossfades into it.
      narrate(state.page);
    }
  }

  store.subscribe(sync);

  return {
    hasStory: () => story !== null,

    // A cover without a published story.json: the page timer stands in.
    clearStory() {
      story = null;
      narratingPage = null;
    },

    // The cover tap: prefetch everything, offer resume if the story was
    // left unfinished, otherwise speak the start prompt and begin page 1.
    async openStory(loaded) {
      story = loaded;
      narratingPage = null;
      prefetcher?.prefetchStory(loaded); // fire and forget; load() dedupes

      const choiceIndex = loaded.pages.findIndex((page) => page.choice);
      starting = true;
      store.openStory({
        pageCount: loaded.pages.length,
        choicePage: choiceIndex === -1 ? null : choiceIndex,
      });

      if (store.state.resumeOpen || !prompts.story_start) {
        starting = false;
        sync(store.state);
        return;
      }

      const release = () => {
        if (!starting) return;
        starting = false;
        sync(store.state);
      };
      try {
        await engine.playPrompt(prompts.story_start, { onEnded: release });
      } catch {
        release(); // a lost prompt never blocks the story
      }
    },
  };
}
