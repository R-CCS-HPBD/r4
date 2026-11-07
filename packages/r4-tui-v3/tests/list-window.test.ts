/**
 * adjustListWindow — bounded scrolling window for picker lists.
 * The selected row must always land inside the visible slice (the palette
 * used to let selection walk off into clipped, unreachable rows).
 */

import { describe, expect, test } from "bun:test";
import { adjustListWindow } from "../src/lib/list-window";

describe("adjustListWindow", () => {
  test("short lists show everything from 0", () => {
    const w = adjustListWindow(5, 2, 0, 19);
    expect(w).toEqual({ start: 0, end: 5, offset: 0 });
  });

  test("selection near the top keeps the window at 0", () => {
    expect(adjustListWindow(100, 0, 0, 19).start).toBe(0);
    expect(adjustListWindow(100, 10, 0, 19).start).toBe(0);
  });

  test("selection past the window bottom slides the window down", () => {
    const w = adjustListWindow(100, 25, 0, 19);
    expect(w.start).toBe(25 - 19 + 1);
    expect(w.end).toBe(26);
    expect(w.end - w.start).toBe(19);
  });

  test("moving back up scrolls the window up again", () => {
    const down = adjustListWindow(100, 25, 0, 19);
    const up = adjustListWindow(100, 3, down.offset, 19);
    expect(up.start).toBe(3);
    expect(up.end).toBe(22);
  });

  test("offset clamps at the list tail", () => {
    const w = adjustListWindow(100, 99, 200, 19);
    expect(w.start).toBe(81);
    expect(w.end).toBe(100);
    expect(w.offset).toBe(81);
  });

  test("empty list is a stable no-op", () => {
    expect(adjustListWindow(0, 0, 0, 19)).toEqual({ start: 0, end: 0, offset: 0 });
  });

  test("selection always inside the window across a full sweep", () => {
    const cap = 19;
    let offset = 0;
    for (let sel = 0; sel < 60; sel++) {
      const w = adjustListWindow(60, sel, offset, cap);
      expect(sel).toBeGreaterThanOrEqual(w.start);
      expect(sel).toBeLessThan(w.end);
      offset = w.offset;
    }
    for (let sel = 59; sel >= 0; sel--) {
      const w = adjustListWindow(60, sel, offset, cap);
      expect(sel).toBeGreaterThanOrEqual(w.start);
      expect(sel).toBeLessThan(w.end);
      offset = w.offset;
    }
  });
});
