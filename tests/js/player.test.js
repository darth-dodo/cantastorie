import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { init } from "../../src/static/js/main.js";

// Vitest runs with cwd at the project root; import.meta.url is an http://
// URL inside the jsdom environment, so resolve from cwd instead. The FastAPI
// shell serves this template at "/" and mounts the assets under "/static".
const indexHtml = readFileSync("src/templates/index.html", "utf-8");
const manifest = JSON.parse(readFileSync("src/static/content/it/manifest.json", "utf-8"));

const manifestFetch = async () => ({ ok: true, json: async () => manifest });
const brokenFetch = async () => ({ ok: false, status: 503 });

let running = null;

afterEach(() => {
  running?.stop();
  running = null;
  localStorage.clear();
});

describe("player shell", () => {
  it("index.html mounts an #app root, the stylesheets, and the asset base", () => {
    document.documentElement.innerHTML = indexHtml;
    expect(document.querySelector("#app")).not.toBeNull();
    expect(document.querySelector('link[href="/static/css/tokens.css"]')).not.toBeNull();
    expect(document.querySelector('link[href="/static/css/player.css"]')).not.toBeNull();
    expect(document.querySelector('meta[name="asset-base"]').content).toBe("/static/content");
  });

  it("boots a manifest-driven shelf", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch });
    expect(running.manifestLoaded).toBe(true);
    const covers = [...document.querySelectorAll(".shelf .cover")];
    expect(covers.map((c) => c.getAttribute("aria-label"))).toEqual(
      manifest.stories.map((s) => s.title),
    );
  });

  it("falls back to the built-in covers when the manifest is unreachable", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: brokenFetch });
    expect(running.manifestLoaded).toBe(false);
    expect(document.querySelectorAll(".shelf .cover")).toHaveLength(4);
  });

  it("a cover tap opens the player with beads and the play-pause control", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = await init(document, { fetchFn: manifestFetch });
    document.querySelector(".cover").click();
    expect(document.querySelector(".player")).not.toBeNull();
    expect(document.querySelectorAll(".bead")).toHaveLength(8);
    expect(document.querySelector(".play-pause")).not.toBeNull();
    expect(document.querySelector(".page-wash.current").dataset.page).toBe("0");
  });
});
