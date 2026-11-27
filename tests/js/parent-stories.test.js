import { beforeEach, describe, expect, it } from "vitest";
import { wireParentStories } from "../../src/static/js/parent-stories.js";

// The parent stories page's filter toggle and two-step delete, moved out of an
// inline <script> so script-src needs no 'unsafe-inline' (AI-505).

function render() {
  document.body.innerHTML = `
    <div data-filter-wrap>
      <button type="button" data-filter-toggle>Filter</button>
      <div data-filter-panel hidden>panel</div>
    </div>
    <div data-arm-wrap>
      <button type="button" data-arm-trigger>x</button>
      <button type="submit" data-arm-confirm hidden>Delete</button>
    </div>`;
  wireParentStories(document);
  return {
    toggle: document.querySelector("[data-filter-toggle]"),
    panel: document.querySelector("[data-filter-panel]"),
    trigger: document.querySelector("[data-arm-trigger]"),
    confirm: document.querySelector("[data-arm-confirm]"),
  };
}

describe("parent stories page", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("opens and closes the filter panel from its toggle", () => {
    const { toggle, panel } = render();
    toggle.click();
    expect(panel.hidden).toBe(false);
    toggle.click();
    expect(panel.hidden).toBe(true);
  });

  it("closes the filter panel on a click anywhere else", () => {
    const { toggle, panel } = render();
    toggle.click();
    document.body.click();
    expect(panel.hidden).toBe(true);
  });

  it("arms delete on the first tap and shows the confirm button", () => {
    const { trigger, confirm } = render();
    trigger.click();
    expect(trigger.hidden).toBe(true);
    expect(confirm.hidden).toBe(false);
  });
});
