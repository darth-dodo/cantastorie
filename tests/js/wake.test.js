import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createWaker } from "../../src/static/js/wake.js";

// Wake-on-activation (docs/plans/2026-09-27-audio-wake.md, Task 2): every
// activation-triggering event re-arms the AudioContext, and the page
// returning to view gets a best-effort unlock too. The greeting fires once
// per page life — the first unlock that actually lands decides that,
// synchronously, so a single tap's several events (pointerdown plus
// pointerup/touchend plus click) never double-greet.

function fakeEngine() {
  return {
    unlocked: false,
    unlock: vi.fn(async () => {}),
  };
}

let root;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  root.remove();
  delete document.visibilityState;
});

function setVisibility(state) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
}

describe("createWaker", () => {
  it.each(["pointerdown", "pointerup", "touchend", "click", "keydown"])(
    "%s on the root calls engine.unlock()",
    (type) => {
      const engine = fakeEngine();
      const waker = createWaker({ engine, root, doc: document, onFirstUnlock: vi.fn() });

      root.dispatchEvent(new Event(type, { bubbles: true }));

      expect(engine.unlock).toHaveBeenCalledTimes(1);
      waker.dispose();
    },
  );

  it("keeps calling unlock() on repeated events — there is no once", () => {
    const engine = fakeEngine();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock: vi.fn() });

    root.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    root.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    root.dispatchEvent(new Event("click", { bubbles: true }));

    expect(engine.unlock).toHaveBeenCalledTimes(3);
    waker.dispose();
  });

  it("one gesture firing pointerup, touchend and click greets exactly once", async () => {
    const engine = fakeEngine();
    engine.unlock.mockImplementation(async () => {
      engine.unlocked = true;
    });
    const onFirstUnlock = vi.fn();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock });

    root.dispatchEvent(new Event("pointerup", { bubbles: true }));
    root.dispatchEvent(new Event("touchend", { bubbles: true }));
    root.dispatchEvent(new Event("click", { bubbles: true }));

    await vi.waitFor(() => expect(onFirstUnlock).toHaveBeenCalledTimes(1));
    // Flush any stray microtasks so a second, wrong call would surface here.
    await Promise.resolve();
    await Promise.resolve();
    expect(onFirstUnlock).toHaveBeenCalledTimes(1);
    waker.dispose();
  });

  it("an unlock that resolves while still locked doesn't greet; the next successful one does", async () => {
    const engine = fakeEngine();
    let calls = 0;
    engine.unlock.mockImplementation(async () => {
      calls += 1;
      if (calls > 1) engine.unlocked = true;
    });
    const onFirstUnlock = vi.fn();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock });

    root.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await vi.waitFor(() => expect(engine.unlock).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(onFirstUnlock).not.toHaveBeenCalled();

    root.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await vi.waitFor(() => expect(onFirstUnlock).toHaveBeenCalledTimes(1));
    waker.dispose();
  });

  it('visibilitychange calls unlock() when visibilityState is "visible"', () => {
    const engine = fakeEngine();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock: vi.fn() });
    setVisibility("visible");

    document.dispatchEvent(new Event("visibilitychange"));

    expect(engine.unlock).toHaveBeenCalledTimes(1);
    waker.dispose();
  });

  it('visibilitychange does not call unlock() when visibilityState is "hidden"', () => {
    const engine = fakeEngine();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock: vi.fn() });
    setVisibility("hidden");

    document.dispatchEvent(new Event("visibilitychange"));

    expect(engine.unlock).not.toHaveBeenCalled();
    waker.dispose();
  });

  it("dispose() removes every listener", () => {
    const engine = fakeEngine();
    const waker = createWaker({ engine, root, doc: document, onFirstUnlock: vi.fn() });

    waker.dispose();

    for (const type of ["pointerdown", "pointerup", "touchend", "click", "keydown"]) {
      root.dispatchEvent(new Event(type, { bubbles: true }));
    }
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));

    expect(engine.unlock).not.toHaveBeenCalled();
  });
});
