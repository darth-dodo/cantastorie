// Wake gesture handling (docs/plans/2026-09-27-audio-wake.md, B9). Browsers
// hold the AudioContext suspended until a real user gesture resumes it, and
// iOS/Safari can drop a running context back to "interrupted" with no event
// at all — the tab backgrounds, a call comes in, another app plays audio. A
// single pointerdown listener that fires once misses touch activation
// entirely and never retries after an interruption. This waker asks the
// engine to unlock on every activation-triggering event for as long as it's
// attached, and again, best-effort, whenever the page returns to view.

// pointerdown is the activation event for a mouse; pointerup and touchend
// are the activation events for touch (HTML Standard, activation-triggering
// input events). click and keydown cover keyboard and synthetic activation.
const GESTURE_EVENTS = ["pointerdown", "pointerup", "touchend", "click", "keydown"];

export function createWaker({ engine, root, doc, onFirstUnlock }) {
  let greeted = false;

  function wake(event) {
    engine
      .unlock()
      .then(() => {
        if (!engine.unlocked || greeted) return;
        // Checked and flipped synchronously, in the same microtask as the
        // unlocked check above: one tap fires pointerdown/pointerup/touchend
        // plus click, and this guard is what keeps that to one greeting.
        greeted = true;
        onFirstUnlock(event);
      })
      .catch(() => {});
  }

  function onVisibilityChange() {
    if (doc.visibilityState === "visible") {
      engine.unlock().catch(() => {});
    }
  }

  // Capture, no `once` — every activation re-arms the wake, not just the
  // first, so a dropped context always gets another chance on the next tap.
  for (const type of GESTURE_EVENTS) {
    root.addEventListener(type, wake, { capture: true });
  }
  doc.addEventListener("visibilitychange", onVisibilityChange);

  return {
    dispose() {
      for (const type of GESTURE_EVENTS) {
        root.removeEventListener(type, wake, { capture: true });
      }
      doc.removeEventListener("visibilitychange", onVisibilityChange);
    },
  };
}
