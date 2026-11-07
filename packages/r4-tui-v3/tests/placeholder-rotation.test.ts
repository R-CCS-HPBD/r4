import { describe, expect, it } from "bun:test";
import { PLACEHOLDER_BUCKET_MS, PLACEHOLDER_HINTS, rotatingPlaceholder } from "../src/lib/placeholder-rotation";

describe("D5a placeholder rotation", () => {
  it("rotates through the hint list by time bucket", () => {
    for (let i = 0; i < PLACEHOLDER_HINTS.length; i += 1) {
      expect(rotatingPlaceholder(i * PLACEHOLDER_BUCKET_MS)).toBe(PLACEHOLDER_HINTS[i]);
    }
  });

  it("wraps around after the full cycle", () => {
    const wrap = PLACEHOLDER_HINTS.length * PLACEHOLDER_BUCKET_MS;
    expect(rotatingPlaceholder(wrap)).toBe(PLACEHOLDER_HINTS[0]);
    expect(rotatingPlaceholder(wrap + PLACEHOLDER_BUCKET_MS)).toBe(PLACEHOLDER_HINTS[1]);
  });

  it("stays stable inside a bucket", () => {
    expect(rotatingPlaceholder(1)).toBe(rotatingPlaceholder(PLACEHOLDER_BUCKET_MS - 1));
  });
});
