/* palette-resolve.js — pure palette/theme resolution logic.
   Importable ES module; used by palette.js (inline) and tested by vitest. */

export const VALID_PALETTES = ["warm", "indigo", "seaglass", "plum"];

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

/**
 * Resolve the active theme.
 * @param {string} search - location.search string
 * @param {number} [hour] - current hour (0–23); defaults to new Date().getHours()
 * @returns {"light"|"dusk"}
 */
export function resolveTheme(search, hour) {
  const params = new URLSearchParams(search || "");
  const t = params.get("theme");
  if (t === "light" || t === "dusk") return t;
  const h = hour !== undefined ? hour : new Date().getHours();
  return h >= 19 ? "dusk" : "light";
}
