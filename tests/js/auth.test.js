import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function mountDom(bodyHtml, door) {
  document.body.innerHTML = bodyHtml;
  document.body.setAttribute("data-auth-door", door);
}

function fakeClerk(overrides = {}) {
  window.__internal_ClerkUICtor = function ClerkUI() {};
  window.Clerk = {
    load: vi.fn().mockResolvedValue(undefined),
    mountSignIn: vi.fn(),
    signOut: vi.fn(),
    isSignedIn: false,
    user: null,
    ...overrides,
  };
}

async function loadAuth() {
  vi.resetModules();
  await import("../../src/static/js/auth.js");
  // the module's init() is async; flush a microtask + its first timer tick
  await new Promise((r) => setTimeout(r, 60));
}

describe("auth.js", () => {
  beforeEach(() => {
    // fresh body each test: repeated module imports stack body-level listeners
    document.documentElement.replaceChild(document.createElement("body"), document.body);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.Clerk;
    delete window.__internal_ClerkUICtor;
  });

  it("mounts sign-in on an unauthenticated sign-in page", async () => {
    mountDom('<div id="clerk-sign-in" data-clerk-mount></div>', "workshop");
    fakeClerk();
    await loadAuth();
    expect(window.Clerk.load).toHaveBeenCalled();
    expect(window.Clerk.mountSignIn).toHaveBeenCalledWith(
      expect.any(Element),
      { afterSignInUrl: "/workshop", afterSignUpUrl: "/workshop" }
    );
  });

  it("reloads instead of mounting when already signed in", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom('<div data-clerk-mount></div>', "parent");
    fakeClerk({ isSignedIn: true });
    await loadAuth();
    expect(window.Clerk.mountSignIn).not.toHaveBeenCalled();
    expect(reload).toHaveBeenCalled();
  });

  it("provisions on the parent onboarding screen, then reloads", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom("<p data-parent-onboarding></p>", "parent");
    fakeClerk();
    await loadAuth();
    expect(fetch).toHaveBeenCalledWith("/parent/api/provision",
      expect.objectContaining({ method: "POST" }));
    expect(reload).toHaveBeenCalled();
  });

  it("reveals the fallback when Clerk.load fails", async () => {
    mountDom('<p data-signin-fallback hidden></p><div data-clerk-mount></div>', "workshop");
    fakeClerk({ load: vi.fn().mockRejectedValue(new Error("down")) });
    await loadAuth();
    expect(document.querySelector("[data-signin-fallback]").hidden).toBe(false);
  });

  it("wires sign-out on authed pages", async () => {
    mountDom('<button id="ws-signout" hidden></button>', "parent");
    fakeClerk({ user: {} });
    await loadAuth();
    const btn = document.getElementById("ws-signout");
    expect(btn.hidden).toBe(false);
    btn.click();
    expect(window.Clerk.signOut).toHaveBeenCalledWith({ redirectUrl: "/parent" });
  });

  it("reloads on HTMX 401, not on other errors", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", { value: { reload }, writable: true });
    mountDom("", "workshop");
    fakeClerk({ user: {} });
    await loadAuth();
    document.body.dispatchEvent(new CustomEvent("htmx:responseError",
      { detail: { xhr: { status: 401 } } }));
    document.body.dispatchEvent(new CustomEvent("htmx:responseError",
      { detail: { xhr: { status: 500 } } }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
