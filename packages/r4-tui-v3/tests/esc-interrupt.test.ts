/**
 * Esc stops a running turn only on a repeated tap (maintainer 2026-11-03:
 * "esc can be miss tapped very easily"). Before this a single Esc called
 * interruptChat at once (chat.tsx handleEscapeKey).
 */

import { afterEach, describe, expect, it } from "bun:test";
import {
  ESC_INTERRUPT_WINDOW_MS,
  NO_CHORD,
  escArmedHint,
  escInterruptHint,
  escInterruptTaps,
  escStopKeys,
  escSwallowed,
  escTapStops,
  resetEscInterrupt,
  tapEsc,
} from "../src/lib/esc-interrupt";

afterEach(() => resetEscInterrupt());

describe("Esc interrupt chord", () => {
  it("defaults to two taps; R4_ESC_INTERRUPT_TAPS takes 1..3 only", () => {
    expect(escInterruptTaps({})).toBe(2);
    expect(escInterruptTaps({ R4_ESC_INTERRUPT_TAPS: "1" })).toBe(1);
    expect(escInterruptTaps({ R4_ESC_INTERRUPT_TAPS: " 3 " })).toBe(3);
    for (const bad of ["0", "4", "two", "", "2.5"]) {
      expect(escInterruptTaps({ R4_ESC_INTERRUPT_TAPS: bad })).toBe(2);
    }
  });

  it("one tap never stops a two-tap chord; the second within the window does", () => {
    const first = tapEsc(NO_CHORD, 1_000, 2);
    expect(first.fire).toBe(false);
    expect(first.remaining).toBe(1);
    expect(tapEsc(first.chord, 1_000 + ESC_INTERRUPT_WINDOW_MS, 2).fire).toBe(true);
  });

  it("a second tap after the window starts over", () => {
    const first = tapEsc(NO_CHORD, 1_000, 2);
    const late = tapEsc(first.chord, 1_001 + ESC_INTERRUPT_WINDOW_MS, 2);
    expect(late.fire).toBe(false);
    expect(late.remaining).toBe(1);
  });

  it("three taps: each within the window of the previous one", () => {
    let step = tapEsc(NO_CHORD, 0, 3);
    expect(step.remaining).toBe(2);
    step = tapEsc(step.chord, 800, 3);
    expect(step.remaining).toBe(1);
    expect(tapEsc(step.chord, 1_600, 3).fire).toBe(true);
  });

  it("one tap stops at once when the user sets 1 (the old behaviour)", () => {
    expect(tapEsc(NO_CHORD, 0, 1).fire).toBe(true);
  });

  it("the first tap arms a visible hint; the stopping tap clears it and swallows the next taps", () => {
    expect(escTapStops(10_000, 2)).toBe(false);
    expect(escInterruptHint()).toBe("press Esc again to stop");
    expect(escTapStops(10_300, 2)).toBe(true);
    expect(escInterruptHint()).toBe("");
    expect(escSwallowed(10_400)).toBe(true);
    expect(escSwallowed(10_300 + ESC_INTERRUPT_WINDOW_MS)).toBe(false);
  });

  it("names the chord the placeholders show", () => {
    expect(escStopKeys(1)).toBe("Esc");
    expect(escStopKeys(2)).toBe("Esc Esc");
    expect(escStopKeys(3)).toBe("Esc ×3");
    expect(escArmedHint(2)).toBe("press Esc 2 more times to stop");
  });
});
