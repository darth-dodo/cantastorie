/* palette-resolve.js — pure palette/theme resolution logic.
   Importable ES module; used by palette.js (inline) and tested by vitest. */

export const VALID_PALETTES = ["indigo"];

/**
 * Resolve the active palette name.
 * @param {string} search  - location.search string (e.g. "?palette=warm")
 * @param {string|null} stored - value from localStorage (may be null)
 * @returns {string} palette name
 */
export function resolvePalette(search, stored) {
  const params = new URLSearchParams(search || "");
  const fromParam = params.get("palette");
  if (fromParam && VALID_PALETTES.includes(fromParam)) return fromParam;
  if (stored && VALID_PALETTES.includes(stored)) return stored;
  return "indigo";
}

/** localStorage key for the Light choice: "light" | "dusk" | "auto". */
export const THEME_KEY = "cantastorie-theme";
export const THEME_MODES = ["light", "dusk", "auto"];

/**
 * Resolve the active theme. `?theme=` wins; then the stored Light choice;
 * the default is dusk (AI-459). "auto" (settings "By itself") is dusk from
 * 19:00 until 07:00, light in between.
 * @param {string} search - location.search string
 * @param {number} [hour] - current hour (0–23); only used when mode === "auto"
 * @param {string|null} [mode] - the stored Light choice
 * @returns {"light"|"dusk"}
 */
export function resolveTheme(search, hour, mode) {
  const params = new URLSearchParams(search || "");
  const t = params.get("theme");
  if (t === "light" || t === "dusk") return t;
  if (mode === "light" || mode === "dusk") return mode;
  if (mode === "auto") {
    const h = hour !== undefined ? hour : new Date().getHours();
    return h >= 19 || h < 7 ? "dusk" : "light";
  }
  return "dusk";
}

/** The stored Light choice, or null (storage may be unavailable). */
export function loadThemeMode(storage = globalThis.localStorage) {
  try {
    const mode = storage?.getItem(THEME_KEY);
    return THEME_MODES.includes(mode) ? mode : null;
  } catch {
    return null;
  }
}

export function saveThemeMode(mode, storage = globalThis.localStorage) {
  try {
    storage?.setItem(THEME_KEY, mode);
  } catch {
    /* private mode: the choice lasts this page only */
  }
}
