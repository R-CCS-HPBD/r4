/**
 * Wave G — tier-1 inline sparklines.
 */

import { describe, expect, test } from "bun:test";
import { sparkline, sparklineRow } from "../src/lib/sparkline";

describe("sparkline", () => {
  test("ramp renders increasing steps", () => {
    const s = sparkline([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(s).toBe("▁▂▃▄▅▆▇█");
  });

  test("flat series renders at the lowest tick", () => {
    expect(sparkline([3, 3, 3])).toBe("▁▁▁");
  });

  test("empty series outputs an honest gap marker", () => {
    expect(sparkline([])).toBe("—");
  });

  test("width trims to the newest values", () => {
    const s = sparkline([0, 1, 2, 11, 12], 3);
    expect(s.length).toBe(3);
    // Normalized across the trimmed window [2,11,12] → low at 2, high at 12
    expect(s.endsWith("█")).toBe(true);
  });
});

describe("sparklineRow", () => {
  test("label + space + ticks fit the width", () => {
    const row = sparklineRow("load", [1, 2, 3], 12);
    expect(row.startsWith("load ")).toBe(true);
    expect(row.length).toBeLessThanOrEqual(12);
  });
});
