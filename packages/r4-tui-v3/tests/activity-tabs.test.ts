import { describe, expect, it, beforeEach } from "bun:test";
import {
  ACTIVITY_TABS,
  ACTIVITY_TAB_DIGIT_KEYS,
  ACTIVITY_TAB_CYCLE_KEYS,
  cycleActivityTab,
  gotoActivityTab,
  gotoActivityTabByIndex,
} from "../src/lib/activity-tabs";
import { actions, workspace } from "../src/stores";

describe("activity tabs keyboard", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    if (workspace.activityOpen) actions.toggleActivityConsole();
  });

  it("exports diagnostic tabs and digit jumps (no letter / ctrl+alt chords)", () => {
    expect(ACTIVITY_TABS).toEqual([
      "agent-logs",
      "tool-calls",
      "events",
      "terminal",
      "notifications",
      "todos",
    ]);
    expect(ACTIVITY_TAB_DIGIT_KEYS).toEqual(["1", "2", "3", "4", "5", "6"]);
    expect(ACTIVITY_TAB_CYCLE_KEYS.prev).toContain("ctrl+pageup");
    expect(ACTIVITY_TAB_CYCLE_KEYS.next).toContain("ctrl+pagedown");
  });

  it("cycle opens console and advances tab", () => {
    expect(workspace.activityOpen).toBe(false);
    expect(cycleActivityTab(1)).toBe(true);
    expect(workspace.activityOpen).toBe(true);
    const first = workspace.activityTab;
    cycleActivityTab(1);
    expect(workspace.activityTab).not.toBe(first);
  });

  it("goto by index jumps to terminal (4)", () => {
    expect(gotoActivityTabByIndex(4)).toBe(true);
    expect(workspace.activityTab).toBe("terminal");
    expect(workspace.activityOpen).toBe(true);
  });

  it("gotoActivityTab sets notifications", () => {
    gotoActivityTab("notifications");
    expect(workspace.activityTab).toBe("notifications");
  });
});
