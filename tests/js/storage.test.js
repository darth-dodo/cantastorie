import { describe, expect, it } from "vitest";
import { load, save } from "../../src/static/js/storage.js";

// A tiny in-memory Storage stand-in — the same shape localStorage exposes,
// so save/load can round-trip without a browser.
function memStorage(seed = {}) {
  const store = new Map(Object.entries(seed));
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    _dump: () => store,
  };
}

const KEY = "cantastorie-shell";

describe("progress persistence", () => {
  it("round-trips the recorded choices through save/load (AI-428)", () => {
    const storage = memStorage();
    save({ screen: "player", page: 7, choices: [1] }, storage);
    const restored = load(storage);
    expect(restored.choices).toEqual([1]);
    expect(restored.page).toBe(7);
  });

  it("defaults choices to [] for OLD payloads written before branches", () => {
    // A save from before Task 12: no choices key at all.
    const storage = memStorage({
      [KEY]: JSON.stringify({ screen: "player", page: 3 }),
    });
    const restored = load(storage);
    expect(restored.choices).toEqual([]);
    expect(restored.page).toBe(3);
  });

  it("never throws — a blocked or garbage store returns null, not an error", () => {
    const blocked = {
      getItem: () => "{not json",
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(() => save({ screen: "player", page: 1 }, blocked)).not.toThrow();
    expect(load(blocked)).toBeNull();
  });
});
