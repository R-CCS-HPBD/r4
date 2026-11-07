/**
 * B2 — Scroll anchoring + new-content pill helpers.
 */

import { describe, expect, test } from "bun:test";
import { isAtBottom, newContentCount, newContentPillLabel } from "../src/lib/scroll-follow";

describe("isAtBottom", () => {
  test("exact bottom and within slack count as bottom", () => {
    expect(isAtBottom(40, 20, 60)).toBe(true);
    expect(isAtBottom(39, 20, 60)).toBe(true); // 1 short, slack=1
  });
  test("above bottom (beyond slack) is not bottom", () => {
    expect(isAtBottom(10, 20, 60)).toBe(false);
    // Genuinely scrolled up with a tall content list: 0 + 2 << 10 - 1.
    expect(isAtBottom(0, 2, 10)).toBe(false);
  });
  test("empty content is bottom", () => {
    expect(isAtBottom(0, 20, 0)).toBe(true);
  });
});

describe("newContentCount", () => {
  test("counts rows added since anchoring", () => {
    expect(newContentCount(10, 7)).toBe(3);
    expect(newContentCount(7, 7)).toBe(0);
  });
  test("never negative on transcript truncation", () => {
    expect(newContentCount(3, 7)).toBe(0);
  });
});

describe("newContentPillLabel", () => {
  test("labels with count and verb", () => {
    expect(newContentPillLabel(3)).toBe("↓ 3 new — End to jump to latest");
    expect(newContentPillLabel(1)).toContain("↓ 1 new");
    expect(newContentPillLabel(1)).toContain("End");
  });
});
