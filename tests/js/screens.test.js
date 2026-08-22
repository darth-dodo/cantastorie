import { describe, expect, it } from "vitest";
import { buildChoiceOverlay } from "../../src/static/js/screens.js";

// Choice-card rendering (AI-428). buildChoiceOverlay(view, store, onChoose):
// when the loaded story's options carry resolved card_image URLs, each card
// renders an <img class="choice-card-image">; otherwise the CSS wash face
// stays as the fallback (the dev fixture and mock shelf have no cards).

const store = { choose: () => {} };

// A choice view as toPlayable produces it: options carry a resolved
// card_image URL and a label (the button's aria-label carries the label).
const viewWithCards = {
  prompt: "which way?",
  options: [
    { label: "the lantern", card_image: "/s/p6.opt0.webp", audioUrl: "/s/p6.opt0.wav", next_page: "a1" },
    { label: "the boat", card_image: "/s/p6.opt1.webp", audioUrl: "/s/p6.opt1.wav", next_page: "b1" },
  ],
};

// The dev fixture / mock shelf: options with no card images, only washes.
const viewWithWashes = {
  prompt: "which way?",
  options: [
    { label: "the lantern", card_image: null, audioUrl: null, wash: "wash-lanterna", next_page: "a1" },
    { label: "the boat", card_image: null, audioUrl: null, wash: "wash-barchetta-notte", next_page: "b1" },
  ],
};

describe("choice cards render option images (AI-428)", () => {
  it("options with card images render them", () => {
    const overlay = buildChoiceOverlay(viewWithCards, store, undefined);
    const imgs = overlay.querySelectorAll("img.choice-card-image");
    expect(imgs.length).toBe(2);
    expect(imgs[0].src).toContain("p6.opt0");
    // The label rides on the button's aria-label; the image is decorative.
    expect(imgs[0].getAttribute("alt")).toBe("");
    const buttons = overlay.querySelectorAll("button.option");
    expect(buttons[0].getAttribute("aria-label")).toBe("the lantern");
  });

  it("options without card images keep the wash", () => {
    const overlay = buildChoiceOverlay(viewWithWashes, store, undefined);
    expect(overlay.querySelectorAll("img").length).toBe(0);
    // The wash face survives as the fallback.
    expect(overlay.querySelector(".choice-card.wash-lanterna")).not.toBeNull();
  });

  it("falls back to the mock choice when no view is given (story-less cover)", () => {
    const overlay = buildChoiceOverlay(undefined, store, undefined);
    expect(overlay.querySelectorAll("button.option").length).toBe(2);
    expect(overlay.querySelectorAll("img").length).toBe(0);
  });
});
