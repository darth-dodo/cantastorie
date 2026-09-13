import { beforeEach, describe, expect, it } from "vitest";
import { adoptFromDocument, writeFamilyTokenToIndexedDB } from "../../src/static/js/family-adopt.js";

// The parent page (Clerk-gated) seeds the family token into the SAME-ORIGIN
// IndexedDB so a child player opened on the same device reads it and merges the
// family overlay. Schema must match readFamilyTokenFromIndexedDB in main.js:
// db "cantastorie" v1, store "family", key "token".

const TOKEN = "a".repeat(32);

// Minimal in-memory IndexedDB that supports exactly the open → (upgrade) →
// success → transaction → objectStore → put/get sequence both the reader and
// writer use. jsdom ships no IndexedDB, so tests inject this via win.
function fakeIDB() {
  const stores = new Map(); // storeName -> Map(key -> value)
  const calls = { opened: [], puts: [] };
  const idb = {
    open(name, version) {
      calls.opened.push([name, version]);
      const req = { onupgradeneeded: null, onsuccess: null, onerror: null, result: null };
      const db = {
        objectStoreNames: { contains: (n) => stores.has(n) },
        createObjectStore(n) {
          stores.set(n, new Map());
        },
        transaction(n) {
          return {
            objectStore(name) {
              const map = stores.get(name);
              return {
                put(value, key) {
                  const r = { onsuccess: null, onerror: null };
                  calls.puts.push([key, value, name]);
                  map.set(key, value);
                  queueMicrotask(() => r.onsuccess?.());
                  return r;
                },
                get(key) {
                  const r = { onsuccess: null, onerror: null, result: undefined };
                  queueMicrotask(() => {
                    r.result = map.get(key);
                    r.onsuccess?.();
                  });
                  return r;
                },
              };
            },
          };
        },
        close() {},
      };
      req.result = db;
      queueMicrotask(() => {
        if (!stores.has("family")) req.onupgradeneeded?.();
        req.onsuccess?.();
      });
      return req;
    },
  };
  return { idb, stores, calls };
}

describe("writeFamilyTokenToIndexedDB", () => {
  it("puts a valid token under family/token", async () => {
    const { idb, stores, calls } = fakeIDB();
    const ok = await writeFamilyTokenToIndexedDB(TOKEN, { indexedDB: idb });
    expect(ok).toBe(true);
    expect(calls.opened).toEqual([["cantastorie", 1]]);
    expect(calls.puts).toEqual([["token", TOKEN, "family"]]);
    // The value lands exactly where main.js's reader looks it up.
    expect(stores.get("family").get("token")).toBe(TOKEN);
  });

  it("rejects a malformed token without opening the database", async () => {
    const { idb, calls } = fakeIDB();
    const ok = await writeFamilyTokenToIndexedDB("not-a-valid-token", { indexedDB: idb });
    expect(ok).toBe(false);
    expect(calls.opened).toEqual([]);
  });

  it("resolves false (never throws) when IndexedDB is unavailable", async () => {
    const ok = await writeFamilyTokenToIndexedDB(TOKEN, {});
    expect(ok).toBe(false);
  });
});

describe("adoptFromDocument", () => {
  it("reads the token from the family-token meta and seeds it", async () => {
    const { idb, stores } = fakeIDB();
    const doc = {
      querySelector: (sel) =>
        sel === 'meta[name="family-token"]' ? { getAttribute: () => TOKEN } : null,
    };
    const ok = await adoptFromDocument(doc, { indexedDB: idb });
    expect(ok).toBe(true);
    expect(stores.get("family").get("token")).toBe(TOKEN);
  });

  it("does nothing when the page carries no family-token meta", async () => {
    const { idb, calls } = fakeIDB();
    const doc = { querySelector: () => null };
    const ok = await adoptFromDocument(doc, { indexedDB: idb });
    expect(ok).toBe(false);
    expect(calls.opened).toEqual([]);
  });
});
