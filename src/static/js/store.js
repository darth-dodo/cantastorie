// Player state machine. Pure transitions over a plain state object —
// the timer (and later, narration audio ending) drives advance().

export const PAGE_COUNT = 8;
export const CHOICE_PAGE = 2; // audio end on p3 opens the choice, not a turn

export function initialState() {
  return {
    screen: "shelf", // shelf | player | end
    page: 0,
    playing: true,
    choiceOpen: false,
    resumeOpen: false,
    choices: [], // option indices picked at each choice page, in order (AI-428)
    audioError: false, // narration failed to load; the sleeping bird holds the stage
    // The open story's shape; a loaded story.json reconfigures both on
    // openStory(). The defaults keep the design-shell mock behavior.
    pageCount: PAGE_COUNT,
    choicePage: CHOICE_PAGE,
  };
}

export function createStore(saved = null) {
  let state = { ...initialState(), ...(saved ?? {}) };
  const listeners = new Set();

  const notify = () => listeners.forEach((fn) => fn(state));

  const set = (patch) => {
    state = { ...state, ...patch };
    notify();
  };

  return {
    get state() {
      return state;
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    // A cover was tapped. A story left unfinished offers the resume choice.
    // A loaded story.json passes its shape; no config keeps the mock's.
    openStory(config = null) {
      const pageCount = config?.pageCount ?? state.pageCount;
      const choicePage = config ? (config.choicePage ?? null) : state.choicePage;
      const unfinished = state.page > 0 && state.page < pageCount;
      if (unfinished) {
        set({
          screen: "player",
          pageCount,
          choicePage,
          resumeOpen: true,
          playing: false,
          choiceOpen: false,
          audioError: false,
        });
      } else {
        set({
          screen: "player",
          pageCount,
          choicePage,
          page: 0,
          playing: true,
          choiceOpen: false,
          resumeOpen: false,
          audioError: false,
        });
      }
    },

    // Narration for the current page ended (or the timer stood in for it).
    advance() {
      if (state.audioError) return;
      if (state.screen !== "player" || !state.playing) return;
      if (state.choiceOpen || state.resumeOpen) return;
      if (state.choicePage !== null && state.page === state.choicePage) {
        set({ choiceOpen: true });
      } else if (state.page >= state.pageCount - 1) {
        set({ screen: "end" });
      } else {
        set({ page: state.page + 1 });
      }
    },

    // Manual next-page tap: like advance(), but works while paused — an
    // explicit tap is intent, the auto-turn is not. The branch is never
    // skippable: next on the choice page opens the overlay, it does not pass.
    nextPage() {
      if (state.screen !== "player") return;
      if (state.choiceOpen || state.resumeOpen || state.audioError) return;
      if (state.choicePage !== null && state.page === state.choicePage) {
        set({ choiceOpen: true });
      } else if (state.page >= state.pageCount - 1) {
        set({ screen: "end" });
      } else {
        set({ page: state.page + 1 });
      }
    },

    // Manual previous-page tap. Page 0 has nothing to go back to — a
    // deliberate no-op; the UI hides the button there.
    prevPage() {
      if (state.page <= 0) return;
      if (state.screen !== "player") return;
      if (state.choiceOpen || state.resumeOpen || state.audioError) return;
      set({ page: state.page - 1 });
    },

    // A branch option was tapped. Record the pick (Task 12 replays the path)
    // and keep the exact page: choicePage + 1 advance — the chosen arm's first
    // page must already sit at that index (main.js extends the path first).
    choose(optionIndex = 0) {
      // Turn off the choice page: page === choicePage whenever the overlay is
      // open, so page + 1 is the choice page's next slot. We advance from page,
      // not choicePage, because extendPath may have already moved choicePage to
      // the arm's *next* branch before this runs (the ordering invariant).
      set({
        choiceOpen: false,
        page: state.page + 1,
        choices: [...state.choices, optionIndex],
      });
    },

    togglePlay() {
      set({ playing: !state.playing });
    },

    // The played path grew: a chosen branch arm was appended (AI-428). Only
    // the shape changes — page, playing, and overlays are left untouched so
    // the next auto-turn lands on the arm's first page.
    extendShape({ pageCount, choicePage }) {
      set({ pageCount, choicePage });
    },

    // Narration for the current page failed to load (AI-367). Only the
    // player shows the sleeping bird; a stale failure after exiting is noise.
    audioError() {
      if (state.screen !== "player") return;
      set({ audioError: true });
    },

    // The bird was tapped: clear the error and play — sync() re-narrates.
    retryAudio() {
      if (!state.audioError) return;
      set({ audioError: false, playing: true });
    },

    exitStory() {
      // Page is kept: reopening offers "Continuiamo o ricominciamo?"
      set({ screen: "shelf", playing: false, choiceOpen: false, resumeOpen: false, choices: [], audioError: false });
    },

    resumeContinue() {
      set({ resumeOpen: false, playing: true });
    },

    resumeRestart() {
      set({ resumeOpen: false, page: 0, playing: true, choices: [] });
    },

    replay() {
      set({ screen: "player", page: 0, playing: true, choiceOpen: false, resumeOpen: false, choices: [], audioError: false });
    },

    toShelf() {
      set({ screen: "shelf", page: 0, playing: true, choiceOpen: false, resumeOpen: false, choices: [], audioError: false });
    },
  };
}
