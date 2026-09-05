import { test, expect } from "@playwright/test";

// The private family overlay must merge onto the shared shelf in a real
// browser: main.js reads the family token from IndexedDB, fetches the family
// overlay manifest, and appends its stories. No token → shared shelf only, and
// crucially zero overlay requests (never a wasted fetch on the child device).
// Assets come from the local dev fixtures (ASSET_BASE=/static/content, pinned
// by playwright.config.js); the overlay manifest itself is stubbed so the test
// needs no committed family fixtures and no R2.

const TOKEN = "a".repeat(32); // canonical 32-hex family token
const OVERLAY_STORY = {
  id: "overlay-smoke-e2e",
  title: "Overlay Smoke Story",
  wash: "wash-bosco",
  // Point at a real fixture story.json so the entry is fully valid.
  story: "/static/content/en/stories/cosmo-space-cowboy/story.json",
};

async function seedFamilyToken(page, token) {
  await page.evaluate(
    (t) =>
      new Promise((resolve, reject) => {
        const open = indexedDB.open("cantastorie", 1);
        open.onupgradeneeded = () => {
          try {
            open.result.createObjectStore("family");
          } catch {
            // store may already exist — ignore
          }
        };
        open.onsuccess = () => {
          const db = open.result;
          const tx = db.transaction("family", "readwrite");
          tx.objectStore("family").put(t, "token");
          tx.oncomplete = () => {
            db.close();
            resolve();
          };
          tx.onerror = () => {
            db.close();
            reject(tx.error);
          };
        };
        open.onerror = () => reject(open.error);
      }),
    token,
  );
}

test("family overlay merges onto the shared shelf", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // Shared-only first (no token yet) to establish the baseline count.
  await page.goto("/?lang=en", { waitUntil: "networkidle" });
  await page.waitForSelector(".cover-caption");
  const sharedCount = await page.locator(".cover-caption").count();
  expect(sharedCount, "expected the shared EN fixture shelf to render").toBeGreaterThan(0);

  // Seed the token and stub the overlay manifest, then reload so main.js reads
  // the token on boot and merges.
  await seedFamilyToken(page, TOKEN);
  await page.route(`**/families/${TOKEN}/en/manifest.json`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ language: "en", prompts: {}, stories: [OVERLAY_STORY] }),
    }),
  );

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector(".cover-caption");

  const captions = await page.locator(".cover-caption").allTextContents();
  expect(captions.length, "overlay should add exactly one story").toBe(sharedCount + 1);
  expect(captions).toContain("Overlay Smoke Story");
  expect(errors, `no page errors: ${errors.join("; ")}`).toHaveLength(0);
});

test("no family token → shared shelf only, zero overlay requests", async ({ page }) => {
  const overlayRequests = [];
  page.on("request", (r) => {
    if (r.url().includes("/families/")) overlayRequests.push(r.url());
  });

  await page.goto("/?lang=en", { waitUntil: "networkidle" });
  await page.waitForSelector(".cover-caption");

  expect(
    overlayRequests,
    `expected zero overlay fetches without a token, saw: ${overlayRequests.join(", ")}`,
  ).toHaveLength(0);
});
