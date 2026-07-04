// Progress persistence. localStorage now; IndexedDB when real stories land.
// Nothing here ever leaves the browser.

const KEY = "cantastorie-shell";

export function load(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(KEY);
    const saved = raw ? JSON.parse(raw) : null;
    return saved && typeof saved.screen === "string" ? saved : null;
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
