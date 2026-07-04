import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { init } from "../../static/js/main.js";

// Vitest runs with cwd at the project root; import.meta.url is an http://
// URL inside the jsdom environment, so resolve from cwd instead.
const indexHtml = readFileSync("static/index.html", "utf-8");

describe("player shell", () => {
  it("index.html mounts an #app root and the compiled stylesheet", () => {
    document.documentElement.innerHTML = indexHtml;
    expect(document.querySelector("#app")).not.toBeNull();
    expect(document.querySelector('link[href="css/output.css"]')).not.toBeNull();
  });

  it("init marks the app root as ready", () => {
    document.body.innerHTML = '<main id="app"></main>';
    init();
    expect(document.querySelector("#app").dataset.player).toBe("ready");
  });
});
