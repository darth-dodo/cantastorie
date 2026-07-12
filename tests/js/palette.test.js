import { describe, expect, it } from "vitest";
import { resolvePalette, resolveTheme, VALID_PALETTES } from "../../src/static/js/palette-resolve.js";

describe("resolvePalette", () => {
  it("defaults to indigo when no param and no stored value", () => {
    expect(resolvePalette("", null)).toBe("indigo");
    expect(resolvePalette("", undefined)).toBe("indigo");
    expect(resolvePalette("?foo=bar", null)).toBe("indigo");
  });

  it("uses the ?palette= param when valid", () => {
    expect(resolvePalette("?palette=warm", null)).toBe("warm");
    expect(resolvePalette("?palette=seaglass", null)).toBe("seaglass");
    expect(resolvePalette("?palette=plum", null)).toBe("plum");
    expect(resolvePalette("?palette=indigo", null)).toBe("indigo");
  });

  it("ignores unknown ?palette= values and falls back", () => {
    expect(resolvePalette("?palette=rainbow", null)).toBe("indigo");
    expect(resolvePalette("?palette=rainbow", "warm")).toBe("warm");
  });

  it("uses stored value when no valid param is present", () => {
    expect(resolvePalette("", "seaglass")).toBe("seaglass");
    expect(resolvePalette("?theme=dusk", "plum")).toBe("plum");
  });

  it("param takes precedence over stored value", () => {
    expect(resolvePalette("?palette=seaglass", "warm")).toBe("seaglass");
  });

  it("ignores stored values that are not valid palette names", () => {
    expect(resolvePalette("", "rainbow")).toBe("indigo");
    expect(resolvePalette("", "")).toBe("indigo");
  });

  it("valid palettes are warm, indigo, seaglass, plum", () => {
    expect(VALID_PALETTES).toEqual(["warm", "indigo", "seaglass", "plum"]);
  });
});

describe("resolveTheme", () => {
  it("returns 'dusk' for ?theme=dusk, 'light' for ?theme=light", () => {
    expect(resolveTheme("?theme=dusk", 10)).toBe("dusk");
    expect(resolveTheme("?theme=light", 22)).toBe("light");
  });

  it("ignores unknown theme param and falls back to hour rule", () => {
    expect(resolveTheme("?theme=night", 10)).toBe("light");
    expect(resolveTheme("?theme=night", 20)).toBe("dusk");
  });

  it("auto-selects dusk when hour >= 19", () => {
    expect(resolveTheme("", 19)).toBe("dusk");
    expect(resolveTheme("", 23)).toBe("dusk");
    expect(resolveTheme("", 21)).toBe("dusk");
  });

  it("auto-selects light when hour < 19", () => {
    expect(resolveTheme("", 0)).toBe("light");
    expect(resolveTheme("", 18)).toBe("light");
    expect(resolveTheme("", 12)).toBe("light");
    expect(resolveTheme("", 1)).toBe("light");
  });
});
