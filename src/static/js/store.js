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
    openStory() {
      const unfinished = state.page > 0 && state.page < PAGE_COUNT;
      if (unfinished) {
        set({ screen: "player", resumeOpen: true, playing: false, choiceOpen: false });
      } else {
        set({ screen: "player", page: 0, playing: true, choiceOpen: false, resumeOpen: false });
      }
    },

    // Narration for the current page ended (timer stands in for audio).
    advance() {
      if (state.screen !== "player" || !state.playing) return;
      if (state.choiceOpen || state.resumeOpen) return;
      if (state.page === CHOICE_PAGE) {
        set({ choiceOpen: true });
      } else if (state.page >= PAGE_COUNT - 1) {
        set({ screen: "end" });
      } else {
        set({ page: state.page + 1 });
      }
    },

    choose() {
      set({ choiceOpen: false, page: CHOICE_PAGE + 1 });
    },

    togglePlay() {
      set({ playing: !state.playing });
    },

    exitStory() {
      // Page is kept: reopening offers "Continuiamo o ricominciamo?"
      set({ screen: "shelf", playing: false, choiceOpen: false, resumeOpen: false });
    },

    resumeContinue() {
      set({ resumeOpen: false, playing: true });
    },

    resumeRestart() {
      set({ resumeOpen: false, page: 0, playing: true });
    },

    replay() {
      set({ screen: "player", page: 0, playing: true, choiceOpen: false, resumeOpen: false });
    },

    toShelf() {
      set({ screen: "shelf", page: 0, playing: true, choiceOpen: false, resumeOpen: false });
    },
  };
}
