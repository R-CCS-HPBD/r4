import { describe, expect, test } from "bun:test";
import {
  mixHex,
  rippleRowColor,
  shineColumnColors,
} from "../src/lib/logo-animation";

const BASE = "#3050a0";
const BRIGHT = "#ffffff";

describe("mixHex", () => {
  test("endpoints return the inputs", () => {
    expect(mixHex(BASE, BRIGHT, 0)).toBe(BASE);
    expect(mixHex(BASE, BRIGHT, 1)).toBe(BRIGHT);
  });

  test("midpoint mixes each channel", () => {
    expect(mixHex("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(mixHex("#ff0000", "#0000ff", 0.5)).toBe("#800080");
  });

  test("t clamps outside [0, 1]", () => {
    expect(mixHex(BASE, BRIGHT, -1)).toBe(BASE);
    expect(mixHex(BASE, BRIGHT, 2)).toBe(BRIGHT);
  });
});

describe("shineColumnColors", () => {
  test("same tick → same colors; width preserved", () => {
    expect(shineColumnColors(16, 5, BASE, BRIGHT)).toEqual(
      shineColumnColors(16, 5, BASE, BRIGHT),
    );
    expect(shineColumnColors(16, 5, BASE, BRIGHT)).toHaveLength(16);
  });

  test("degenerate width yields no columns", () => {
    expect(shineColumnColors(0, 5, BASE, BRIGHT)).toEqual([]);
    expect(shineColumnColors(-3, 5, BASE, BRIGHT)).toEqual([]);
  });

  test("the sweep moves over ticks and lifts some column", () => {
    const width = 16;
    // Collect the brightest column index across ticks — it must change.
    const brightestAt = (tick: number) => {
      const colors = shineColumnColors(width, tick, BASE, BRIGHT);
      let best = 0;
      let bestIdx = -1;
      colors.forEach((c, i) => {
        const v = parseInt(c.slice(1), 16);
        if (v > best) {
          best = v;
          bestIdx = i;
        }
      });
      return bestIdx;
    };
    const positions = new Set(
      [0, 4, 8, 12, 16, 20, 24, 28, 32, 36].map(brightestAt),
    );
    expect(positions.size).toBeGreaterThan(2);
  });

  test("sweep wraps: a large tick still produces a valid frame", () => {
    const colors = shineColumnColors(16, 10_000, BASE, BRIGHT);
    expect(colors).toHaveLength(16);
    for (const c of colors) expect(c).toMatch(/^#[0-9a-f]{6}$/);
  });

  test("colors stay within the base→bright range (valid hex, ≥ base)", () => {
    const baseVal = parseInt(BASE.slice(1), 16);
    for (const c of shineColumnColors(16, 9, BASE, BRIGHT)) {
      expect(c).toMatch(/^#[0-9a-f]{6}$/);
      // Monotonic per-channel lift toward white.
      expect(parseInt(c.slice(1), 16)).toBeGreaterThanOrEqual(baseVal);
    }
  });
});

describe("rippleRowColor", () => {
  test("deterministic per (row, tick); varies across rows and ticks", () => {
    expect(rippleRowColor(3, 7, BASE, BRIGHT)).toBe(
      rippleRowColor(3, 7, BASE, BRIGHT),
    );
    const rows = new Set(
      [0, 1, 2, 3, 4, 5].map((r) => rippleRowColor(r, 7, BASE, BRIGHT)),
    );
    expect(rows.size).toBeGreaterThan(1);
    const ticks = new Set(
      [0, 3, 6, 9, 12].map((t) => rippleRowColor(2, t, BASE, BRIGHT)),
    );
    expect(ticks.size).toBeGreaterThan(1);
  });

  test("always valid hex within the base→bright envelope", () => {
    for (let tick = 0; tick < 20; tick++) {
      expect(rippleRowColor(5, tick, BASE, BRIGHT)).toMatch(/^#[0-9a-f]{6}$/);
    }
  });
});
