/* Shared auth for Clerk-gated surfaces — one module for /workshop and /parent.
   Sign-in duties run only where the sign-in markup exists; authed pages get
   sign-out wiring and HTMX session-lapse recovery. */
(function () {
  "use strict";

  var DOOR_PATHS = { workshop: "/workshop", parent: "/parent" };
  var doorPath =
    DOOR_PATHS[document.body.getAttribute("data-auth-door")] || "/workshop";

  function revealFallback() {
    var fallback = document.querySelector("[data-signin-fallback]");
    if (fallback) fallback.hidden = false;
  }

  function wireSignOut() {
    var signout = document.getElementById("ws-signout");
    if (signout && window.Clerk.user) {
      signout.hidden = false;
      signout.addEventListener("click", function () {
        window.Clerk.signOut({ redirectUrl: doorPath });
      });
    }
  }

  async function init() {
    if (!window.Clerk) return;
    try {
      await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    } catch (error) {
      revealFallback();
      return;
    }
    var onboarding = document.querySelector("[data-parent-onboarding]");
    if (onboarding) {
      // First parent sign-in: mint-or-link the family token, then re-render.
      try {
        var response = await fetch("/parent/api/provision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (response.ok) window.location.reload();
        else revealFallback();
      } catch (error) {
        revealFallback();
      }
      return;
    }
    var mount = document.querySelector("[data-clerk-mount]");
    if (mount) {
      if (window.Clerk.isSignedIn) {
        window.location.reload();
        return;
      }
      window.Clerk.mountSignIn(mount, {
        afterSignInUrl: doorPath,
        afterSignUpUrl: doorPath,
      });
      return;
    }
    wireSignOut();
  }

  init();

  // A lapsed session makes authed HTMX requests 401; a full-page reload
  // drops the user back onto the shared sign-in flow.
  document.body.addEventListener("htmx:responseError", function (e) {
    if (e.detail && e.detail.xhr && e.detail.xhr.status === 401) {
      window.location.reload();
    }
  });
})();
