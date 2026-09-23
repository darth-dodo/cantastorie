// Same-device overlay bridge: seed the family token into IndexedDB from the
// authenticated parent page so a child player opened on the SAME device reads it
// and merges the family's private overlay onto the shared shelf. Like the child
// player, this module stays auth-SDK-free — it only touches same-origin storage.
//
// Schema MUST match readFamilyTokenFromIndexedDB in main.js — db "cantastorie"
// v1, store "family", key "token". This is same-device only: it does not carry
// the token to other devices (cross-device linking is a later "connect device"
// flow). Best-effort and silent — a failure never blocks the parent page.

const FAMILY_TOKEN_RE = /^[0-9a-f]{32}$/;

// Write the token under family/token. Resolves true on success, false on any
// absence, malformed token, or error. Never throws.
export function writeFamilyTokenToIndexedDB(token, win = globalThis) {
  if (!token || !FAMILY_TOKEN_RE.test(token)) return Promise.resolve(false);
  const idb = win?.indexedDB;
  if (!idb) return Promise.resolve(false);
  return new Promise((resolve) => {
    let open;
    try {
      open = idb.open("cantastorie", 1);
    } catch {
      resolve(false);
      return;
    }
    open.onupgradeneeded = () => {
      try {
        open.result.createObjectStore("family");
      } catch {
        // store may already exist on a bumped version — ignore
      }
    };
    open.onerror = () => resolve(false);
    open.onsuccess = () => {
      const db = open.result;
      try {
        if (!db.objectStoreNames.contains("family")) {
          db.close();
          resolve(false);
          return;
        }
        const req = db.transaction("family", "readwrite").objectStore("family").put(token, "token");
        req.onerror = () => {
          db.close();
          resolve(false);
        };
        req.onsuccess = () => {
          db.close();
          resolve(true);
        };
      } catch {
        try {
          db.close();
        } catch {
          // already closed
        }
        resolve(false);
      }
    };
  });
}

// Read the token from the page's family-token meta and seed it. Resolves false
// when the page carries no token (e.g. an operator page).
export function adoptFromDocument(doc = document, win = globalThis) {
  const token = doc.querySelector('meta[name="family-token"]')?.getAttribute("content");
  if (!token) return Promise.resolve(false);
  return writeFamilyTokenToIndexedDB(token, win);
}

// Auto-run on the parent page. Guarded so importing the module in a non-browser
// context (or a test) does not require a DOM. Best-effort: failures are silent.
if (typeof document !== "undefined") {
  adoptFromDocument().catch(() => {});
}
