/* palette.js — synchronous head script (NOT type=module, NOT deferred).
   Sets data-palette and data-theme on <html> before first paint to avoid flash.
   Exposes window.cantastoriePalette = { set(name), current() }.

   Resolution logic lives in palette-resolve.js (importable ES module for tests).
   This file inlines equivalent logic so it can run as a plain sync <script>. */

(function () {
  var VALID_PALETTES = ["indigo"];
  var LS_KEY = "cantastorie-palette";

  function resolvePalette(search, stored) {
    var params = new URLSearchParams(search || "");
    var fromParam = params.get("palette");
    if (fromParam && VALID_PALETTES.indexOf(fromParam) !== -1) return fromParam;
    if (stored && VALID_PALETTES.indexOf(stored) !== -1) return stored;
    return "indigo";
  }

  var THEME_KEY = "cantastorie-theme";

  // Mirrors resolveTheme in palette-resolve.js (tested there).
  function resolveTheme(search, hour, mode) {
    var params = new URLSearchParams(search || "");
    var t = params.get("theme");
    if (t === "light" || t === "dusk") return t;
    if (mode === "light" || mode === "dusk") return mode;
    if (mode === "auto") {
      var h = hour !== undefined ? hour : new Date().getHours();
      return h >= 19 || h < 7 ? "dusk" : "light";
    }
    return "dusk";
  }

  var stored = null;
  try {
    stored = localStorage.getItem(LS_KEY);
  } catch (_) {}

  var themeMode = null;
  try {
    themeMode = localStorage.getItem(THEME_KEY);
  } catch (_) {}

  var palette = resolvePalette(location.search, stored);
  var theme = resolveTheme(location.search, undefined, themeMode);

  // Persist when palette was set via ?palette= param.
  var params = new URLSearchParams(location.search);
  if (VALID_PALETTES.indexOf(params.get("palette")) !== -1) {
    try {
      localStorage.setItem(LS_KEY, palette);
    } catch (_) {}
  }

  document.documentElement.setAttribute("data-palette", palette);
  document.documentElement.setAttribute("data-theme", theme);

  window.cantastoriePalette = {
    /** Switch palette live and persist to localStorage. */
    set: function (name) {
      if (VALID_PALETTES.indexOf(name) === -1) return;
      try {
        localStorage.setItem(LS_KEY, name);
      } catch (_) {}
      document.documentElement.setAttribute("data-palette", name);
    },
    /** Return the currently active palette name. */
    current: function () {
      return document.documentElement.getAttribute("data-palette") || "indigo";
    },
  };
})();
