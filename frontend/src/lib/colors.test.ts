import { describe, expect, it } from "vitest";
import { TYPE_COLORS, UNKNOWN_COLOR, colorForType, hueOf, realmPalette } from "./colors";

describe("colorForType", () => {
  it("uses the named colour for a type the palette knows", () => {
    expect(colorForType("person")).toBe(TYPE_COLORS.person);
  });

  it("derives a stable colour for a type nobody named", () => {
    const a = colorForType("skunkworks");
    expect(a).toMatch(/^hsl\(\d+ 55% 62%\)$/);
    expect(colorForType("skunkworks")).toBe(a);
  });

  it("falls back for the empty type rather than producing hsl(NaN)", () => {
    expect(colorForType("")).toBe(UNKNOWN_COLOR);
  });
});

describe("hueOf", () => {
  it("is deterministic and inside the wheel", () => {
    for (const s of ["a", "task", "a much longer name", "ü"]) {
      const h = hueOf(s);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(360);
      expect(hueOf(s)).toBe(h);
    }
  });
});

describe("realmPalette", () => {
  it("spaces realms evenly rather than hashing them together", () => {
    const paint = realmPalette(["optibus", "personal"]);
    expect(paint("optibus")).not.toBe(paint("personal"));
    const hues = ["optibus", "personal"].map((k) => Number(/hsl\((\d+)/.exec(paint(k))![1]));
    expect(Math.abs(hues[0]! - hues[1]!)).toBe(180);
  });

  it("answers for a realm it was never given", () => {
    expect(realmPalette(["a"])("b")).toBe(UNKNOWN_COLOR);
  });

  it("does not divide by zero on an empty brain", () => {
    expect(() => realmPalette([])("x")).not.toThrow();
  });
});
