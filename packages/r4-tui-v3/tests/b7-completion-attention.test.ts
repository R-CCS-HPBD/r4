/**
 * B7 — Completion attention: bell + title after long turns (opt-out aware).
 * Pure watcher/gate semantics; the OSC/BEL writes are smoke-only.
 */

import { describe, expect, test } from "bun:test";
import {
  COMPLETION_ATTENTION_MIN_MS,
  makeCompletionWatcher,
  shouldSignalCompletion,
} from "../src/lib/completion-attention";

describe("shouldSignalCompletion", () => {
  test("fires at and past the threshold when enabled", () => {
    expect(shouldSignalCompletion(COMPLETION_ATTENTION_MIN_MS, true)).toBe(true);
    expect(shouldSignalCompletion(COMPLETION_ATTENTION_MIN_MS + 1, true)).toBe(true);
  });
  test("short turns never fire", () => {
    expect(shouldSignalCompletion(COMPLETION_ATTENTION_MIN_MS - 1, true)).toBe(false);
  });
  test("opt-out suppresses regardless of duration", () => {
    expect(shouldSignalCompletion(10 * COMPLETION_ATTENTION_MIN_MS, false)).toBe(false);
  });
});

describe("makeCompletionWatcher", () => {
  test("signals elapsed ms on a true→false transition", () => {
    const signals: number[] = [];
    const watch = makeCompletionWatcher((ms) => signals.push(ms));
    watch(true); // turn starts
    watch(false); // turn ends
    expect(signals.length).toBe(1);
    expect(signals[0]).toBeGreaterThanOrEqual(0);
  });

  test("transition without a start is ignored (no phantom signals)", () => {
    const signals: number[] = [];
    const watch = makeCompletionWatcher((ms) => signals.push(ms));
    watch(false);
    expect(signals.length).toBe(0);
  });

  test("each turn is measured independently", () => {
    const signals: number[] = [];
    const watch = makeCompletionWatcher((ms) => signals.push(ms));
    watch(true);
    watch(false);
    watch(true);
    watch(false);
    expect(signals.length).toBe(2);
  });
});
