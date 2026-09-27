// A deterministic 7-cover EN shelf for the shelf-layout regression specs.
// The overlap bug (#59) only shows at 5+ covers and the local EN fixture has
// three, so the EN manifest is stubbed with seven entries shaped exactly like
// the ones publish.py writes (a `cover` URL = the story's first page image).
// The cover art is the local EN fixtures' own page images, so the specs run
// against this checkout's JS + CSS with no dependency on the production bucket.

const EN_STORIES = "/static/content/en/stories";

const ART = [
  ["cosmo-space-cowboy", "p1.82b568b2.webp"],
  ["pip-the-pirate", "p1.8933337a.webp"],
  ["bruno-birthday-bear", "p1.dcb0b466.webp"],
  ["cosmo-space-cowboy", "p2.be9a1642.webp"],
  ["pip-the-pirate", "p2.b9cb217c.webp"],
  ["bruno-birthday-bear", "p2.fed0defa.webp"],
  ["cosmo-space-cowboy", "p3.cc8226a1.webp"],
];

export const SEVEN_COVER_MANIFEST = {
  language: "en",
  prompts: {},
  stories: ART.map(([storyId, image], i) => ({
    id: `shelf-layout-${i + 1}`,
    title: `Shelf Layout Story ${i + 1}`,
    wash: "wash-bosco",
    cover: `${EN_STORIES}/${storyId}/${image}`,
    story: `${EN_STORIES}/${storyId}/story.json`,
  })),
};

// Serve the stubbed manifest, open the EN shelf, and wait for every cover's
// art to finish loading so layout-space measurements are final.
export async function openSevenCoverShelf(page) {
  await page.route("**/en/manifest.json", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(SEVEN_COVER_MANIFEST) }),
  );
  await page.goto("/play?lang=en&theme=light", { waitUntil: "networkidle" });
  await page.waitForSelector(".covers .cover");
  for (const img of await page.locator(".covers img.cover-art").all()) {
    await img.scrollIntoViewIfNeeded();
  }
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".covers img.cover-art")].every(
      (img) => img.complete && img.naturalWidth > 0,
    ),
  );
}
