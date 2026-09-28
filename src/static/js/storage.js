// Progress persistence. localStorage now; IndexedDB when real stories land.
// Nothing here ever leaves the browser.

import { initialState } from "./store.js";

const KEY = "cantastorie-shell";

export function load(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (!saved || typeof saved.screen !== "string") return null;
    // The app always boots to the shelf (AI-468) — a saved screen: "player"
    // would resume with no story loaded (activeStory is only set by tapping
    // a cover), so the mock view plays instead and the story ends in ~30s.
    // Only two fields are real progress worth carrying across a reload:
    // `page` (how far the child got) and `choices` (the branch picks), both
    // read by openCover()/store.openStory() to rebuild the played path and
    // offer the resume overlay when the same cover is tapped again.
    // Everything else — screen, playing, choiceOpen, resumeOpen, audioError,
    // pageCount, choicePage — is either UI state tied to a live player
    // session or shape tied to the specific story that was open, and none of
    // it means anything on the shelf; it's rebuilt from initialState() (or
    // from the reopened story's own config) instead of trusted from disk.
    // Normalizing here, rather than in save(), also cleans up bad/older
    // payloads on the way in, regardless of which version wrote them.
    const page = Number.isInteger(saved.page) && saved.page > 0 ? saved.page : 0;
    const choices = Array.isArray(saved.choices) ? saved.choices : [];
    return { ...initialState(), page, choices };
  } catch {
    return null;
  }
}

export function save(state, storage = globalThis.localStorage) {
  try {
    storage?.setItem(KEY, JSON.stringify(state));
  } catch {
    // Storage full or blocked: playback goes on, progress just isn't kept.
  }
}
