import { describe, expect, it } from "vitest";
import { createStore, CHOICE_PAGE, PAGE_COUNT } from "../../src/static/js/store.js";

function playThrough(store, pages) {
  for (let i = 0; i < pages; i++) store.advance();
}

describe("story state machine", () => {
  it("a fresh story plays from page 0", () => {
    const store = createStore();
    store.openStory();
    expect(store.state).toMatchObject({ screen: "player", page: 0, playing: true });
  });

  it("advance turns pages until the choice page, then opens the choice", () => {
    const store = createStore();
    store.openStory();
    playThrough(store, CHOICE_PAGE);
    expect(store.state.page).toBe(CHOICE_PAGE);
    expect(store.state.choiceOpen).toBe(false);
    store.advance();
    expect(store.state.choiceOpen).toBe(true);
    expect(store.state.page).toBe(CHOICE_PAGE); // the page did NOT turn
  });

  it("choosing closes the overlay and lands on the next page", () => {
    const store = createStore({ screen: "player", page: CHOICE_PAGE, choiceOpen: true });
    store.choose();
    expect(store.state).toMatchObject({ choiceOpen: false, page: CHOICE_PAGE + 1 });
  });

  it("the final page ends the story", () => {
    const store = createStore({ screen: "player", page: PAGE_COUNT - 1, playing: true });
    store.advance();
    expect(store.state.screen).toBe("end");
  });

  it("advance is inert while paused or while an overlay is open", () => {
    const paused = createStore({ screen: "player", page: 4, playing: false });
    paused.advance();
    expect(paused.state.page).toBe(4);

    const choosing = createStore({ screen: "player", page: CHOICE_PAGE, choiceOpen: true });
    choosing.advance();
    expect(choosing.state.page).toBe(CHOICE_PAGE);
  });

  it("reopening an unfinished story offers resume instead of restarting", () => {
    const store = createStore({ screen: "player", page: 4, playing: true });
    store.exitStory();
    expect(store.state.screen).toBe("shelf");
    store.openStory();
    expect(store.state).toMatchObject({ resumeOpen: true, playing: false, page: 4 });
  });

  it("resume continues from the exact page; restart begins again", () => {
    const store = createStore({ screen: "player", page: 4, resumeOpen: true, playing: false });
    store.resumeContinue();
    expect(store.state).toMatchObject({ resumeOpen: false, playing: true, page: 4 });

    const other = createStore({ screen: "player", page: 4, resumeOpen: true, playing: false });
    other.resumeRestart();
    expect(other.state).toMatchObject({ resumeOpen: false, playing: true, page: 0 });
  });

  it("a finished story reopens from the start, not the resume offer", () => {
    const store = createStore({ screen: "end", page: 0 });
    store.toShelf();
    store.openStory();
    expect(store.state).toMatchObject({ screen: "player", page: 0, resumeOpen: false });
  });

  it("replay from the end screen starts the story over", () => {
    const store = createStore({ screen: "end", page: PAGE_COUNT - 1 });
    store.replay();
    expect(store.state).toMatchObject({ screen: "player", page: 0, playing: true });
  });
});

describe("story-configured playback (a loaded story.json sets the shape)", () => {
  it("a linear story never opens the choice overlay: pages turn straight to the end", () => {
    // Given a loaded linear story (every page's choice is null)...
    const store = createStore();
    store.openStory({ pageCount: 8, choicePage: null });
    // ...when narration ends on every page in turn...
    playThrough(store, PAGE_COUNT - 1);
    // ...then no choice ever opened and the last turn reaches the end screen.
    expect(store.state.choiceOpen).toBe(false);
    expect(store.state.page).toBe(PAGE_COUNT - 1);
    store.advance();
    expect(store.state.screen).toBe("end");
  });

  it("a story with a choice page still opens the choice there (AI-370 stays possible)", () => {
    const store = createStore();
    store.openStory({ pageCount: 8, choicePage: 4 });
    playThrough(store, 5);
    expect(store.state).toMatchObject({ page: 4, choiceOpen: true });
    store.choose();
    expect(store.state).toMatchObject({ page: 5, choiceOpen: false });
  });

  it("a shorter story ends after its own page count, not the mock's", () => {
    const store = createStore();
    store.openStory({ pageCount: 3, choicePage: null });
    playThrough(store, 3);
    expect(store.state.screen).toBe("end");
  });

  it("opening without a config keeps the design-shell mock shape", () => {
    const store = createStore();
    store.openStory();
    playThrough(store, CHOICE_PAGE + 1);
    expect(store.state.choiceOpen).toBe(true);
  });
});
