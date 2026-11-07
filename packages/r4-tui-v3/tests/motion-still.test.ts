/**
 * P4 pins: reduced-motion switch — R4_TUI_STILL freezes decorative animation.
 */
import { describe, it, expect } from "bun:test";
import { motionAllowed } from "../src/lib/motion";
import { rotatingPlaceholder, PLACEHOLDER_HINTS, PLACEHOLDER_BUCKET_MS } from "../src/lib/placeholder-rotation";

describe("reduced motion (R4_TUI_STILL)", () => {
  it("motionAllowed defaults true; 1 disables", () => {
    expect(motionAllowed({})).toBe(true);
    expect(motionAllowed({ R4_TUI_STILL: "1" })).toBe(false);
    expect(motionAllowed({ R4_TUI_STILL: "0" })).toBe(true);
  });

  it("placeholder rotates by bucket normally", () => {
    const a = rotatingPlaceholder(0);
    const b = rotatingPlaceholder(PLACEHOLDER_BUCKET_MS);
    expect(a).not.toBe(b);
  });

  it("placeholder pins hint[0] under STILL regardless of bucket", () => {
    const env = { R4_TUI_STILL: "1" };
    expect(rotatingPlaceholder(0, undefined, env)).toBe(PLACEHOLDER_HINTS[0]);
    expect(rotatingPlaceholder(PLACEHOLDER_BUCKET_MS * 3, undefined, env)).toBe(PLACEHOLDER_HINTS[0]);
  });
});
