import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { init } from "../../static/js/main.js";

// Vitest runs with cwd at the project root; import.meta.url is an http://
// URL inside the jsdom environment, so resolve from cwd instead.
const indexHtml = readFileSync("static/index.html", "utf-8");

let running = null;

afterEach(() => {
  running?.stop();
  running = null;
  localStorage.clear();
});

describe("player shell", () => {
  it("index.html mounts an #app root and the design-system stylesheets", () => {
    document.documentElement.innerHTML = indexHtml;
    expect(document.querySelector("#app")).not.toBeNull();
    expect(document.querySelector('link[href="css/tokens.css"]')).not.toBeNull();
    expect(document.querySelector('link[href="css/player.css"]')).not.toBeNull();
  });

  it("boots to the shelf with four story covers", () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = init();
    expect(document.querySelectorAll(".shelf .cover")).toHaveLength(4);
    expect(document.querySelector(".greeting h1").textContent).toMatch(/Ciao!|Buonasera!/);
  });

  it("a cover tap opens the player with beads and the play-pause control", () => {
    document.body.innerHTML = '<main id="app"></main>';
    running = init();
    document.querySelector(".cover").click();
    expect(document.querySelector(".player")).not.toBeNull();
    expect(document.querySelectorAll(".bead")).toHaveLength(8);
    expect(document.querySelector(".play-pause")).not.toBeNull();
    expect(document.querySelector(".page-wash.current").dataset.page).toBe("0");
  });
});
