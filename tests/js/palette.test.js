import { describe, expect, it } from "vitest";
import { resolvePalette, resolveTheme, VALID_PALETTES } from "../../src/static/js/palette-resolve.js";

describe("resolvePalette", () => {
  it("defaults to indigo when no param and no stored value", () => {
    expect(resolvePalette("", null)).toBe("indigo");
    expect(resolvePalette("", undefined)).toBe("indigo");
    expect(resolvePalette("?foo=bar", null)).toBe("indigo");
  });

  it("uses the ?palette=indigo param when valid", () => {
    expect(resolvePalette("?palette=indigo", null)).toBe("indigo");
  });

  it("ignores unknown ?palette= values (warm/seaglass/plum/rainbow) and falls back to indigo", () => {
    expect(resolvePalette("?palette=rainbow", null)).toBe("indigo");
    expect(resolvePalette("?palette=warm", null)).toBe("indigo");
    expect(resolvePalette("?palette=seaglass", null)).toBe("indigo");
    expect(resolvePalette("?palette=plum", null)).toBe("indigo");
  });

  it("uses stored indigo value when no valid param is present", () => {
    expect(resolvePalette("", "indigo")).toBe("indigo");
    expect(resolvePalette("?theme=dusk", "indigo")).toBe("indigo");
  });

  it("?palette=indigo param takes precedence over stored (no-op with single palette)", () => {
    expect(resolvePalette("?palette=indigo", "indigo")).toBe("indigo");
  });

  it("ignores stored values that are not valid palette names", () => {
    expect(resolvePalette("", "rainbow")).toBe("indigo");
    expect(resolvePalette("", "warm")).toBe("indigo");
    expect(resolvePalette("", "seaglass")).toBe("indigo");
    expect(resolvePalette("", "plum")).toBe("indigo");
    expect(resolvePalette("", "")).toBe("indigo");
  });

  it("valid palettes is indigo only", () => {
    expect(VALID_PALETTES).toEqual(["indigo"]);
  });
});

describe("resolveTheme", () => {
  it("returns 'dusk' for ?theme=dusk, 'light' for ?theme=light", () => {
    expect(resolveTheme("?theme=dusk", 10)).toBe("dusk");
    expect(resolveTheme("?theme=light", 22)).toBe("light");
  });

  it("ignores unknown theme param and defaults to dusk", () => {
    expect(resolveTheme("?theme=night", 10)).toBe("dusk");
    expect(resolveTheme("?theme=night", 20)).toBe("dusk");
  });

  it("defaults to dusk with no ?theme param, regardless of hour", () => {
    expect(resolveTheme("", 0)).toBe("dusk");
    expect(resolveTheme("", 10)).toBe("dusk");
    expect(resolveTheme("", 18)).toBe("dusk");
    expect(resolveTheme("", 19)).toBe("dusk");
    expect(resolveTheme("", 23)).toBe("dusk");
  });

  it("auto mode (By itself): dusk when hour >= 19", () => {
    expect(resolveTheme("", 19, "auto")).toBe("dusk");
    expect(resolveTheme("", 23, "auto")).toBe("dusk");
    expect(resolveTheme("", 21, "auto")).toBe("dusk");
  });

  it("auto mode (By itself): dusk through the night, until 07:00", () => {
    expect(resolveTheme("", 0, "auto")).toBe("dusk");
    expect(resolveTheme("", 1, "auto")).toBe("dusk");
    expect(resolveTheme("", 6, "auto")).toBe("dusk");
  });

  it("auto mode (By itself): light from 07:00 until 19:00", () => {
    expect(resolveTheme("", 7, "auto")).toBe("light");
    expect(resolveTheme("", 12, "auto")).toBe("light");
    expect(resolveTheme("", 18, "auto")).toBe("light");
  });

  it("a stored light or dusk choice sticks, regardless of hour", () => {
    expect(resolveTheme("", 22, "light")).toBe("light");
    expect(resolveTheme("", 10, "dusk")).toBe("dusk");
  });

  it("unknown stored modes fall back to the dusk default", () => {
    expect(resolveTheme("", 10, "sepia")).toBe("dusk");
  });

  it("?theme param overrides auto mode", () => {
    expect(resolveTheme("?theme=light", 23, "auto")).toBe("light");
    expect(resolveTheme("?theme=dusk", 10, "auto")).toBe("dusk");
  });
});
