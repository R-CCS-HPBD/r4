/**
 * B1 — Expandable tool roll-ups.
 * The cumulative summary row gains per-tool detail lines and an expand/collapse
 * state toggled by Enter (empty draft) or click; latest summary key is tracked.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { formatDurationMs } from "../src/lib/tool-activity";

function reset() {
  setWorkspace("noticeDetails", {});
  setWorkspace("noticeExpanded", {});
  setWorkspace("lastSummaryKey", null);
}

describe("formatDurationMs", () => {
  test("sub-second stays in ms", () => {
    expect(formatDurationMs(120)).toBe("120ms");
    expect(formatDurationMs(999.4)).toBe("999ms");
  });
  test("seconds with one decimal", () => {
    expect(formatDurationMs(1200)).toBe("1.2s");
    expect(formatDurationMs(59000)).toBe("59.0s");
  });
  test("null/invalid → empty label", () => {
    expect(formatDurationMs(null)).toBe("");
    expect(formatDurationMs(-5)).toBe("");
  });
});

describe("notice details + expansion state", () => {
  beforeEach(reset);

  test("setNoticeDetails stores lines and marks the latest summary key", () => {
    actions.setNoticeDetails("tool-activity-summary", ["✓ $ one", "✓ $ two"]);
    expect(workspace.noticeDetails["tool-activity-summary"]).toEqual(["✓ $ one", "✓ $ two"]);
    expect(workspace.lastSummaryKey).toBe("tool-activity-summary");
  });

  test("toggleNoticeExpanded with explicit key flips state", () => {
    actions.setNoticeDetails("k1", ["a"]);
    expect(actions.toggleNoticeExpanded("k1")).toBe(true);
    expect(workspace.noticeExpanded["k1"]).toBe(true);
    expect(actions.toggleNoticeExpanded("k1")).toBe(true);
    expect(workspace.noticeExpanded["k1"]).toBe(false);
  });

  test("toggleNoticeExpanded default uses lastSummaryKey", () => {
    actions.setNoticeDetails("k2", ["a"]);
    actions.toggleNoticeExpanded();
    expect(workspace.noticeExpanded["k2"]).toBe(true);
  });

  test("toggle without a known key returns false and does nothing", () => {
    expect(actions.toggleNoticeExpanded("ghost")).toBe(false);
    expect(actions.toggleNoticeExpanded()).toBe(false); // no lastSummaryKey
  });
});
