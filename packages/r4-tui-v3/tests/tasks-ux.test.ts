import { describe, expect, test, beforeEach } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { askAboutSelectedTask, handleTaskShortcut } from "../src/lib/space-shortcuts";
import { TASKS_HOW_TO } from "../src/lib/empty-hints";

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  actions.setTasks([
    {
      id: "task-1",
      goalId: "demo08-nl",
      title: "Investigate ML models for scientific data",
      status: "pending",
    },
  ]);
  actions.selectObject("task-1");
  actions.setSpace("tasks");
});

describe("task shortcuts", () => {
  test("how-to strip explains purpose and actions", () => {
    expect(TASKS_HOW_TO).toContain("Enter");
    expect(TASKS_HOW_TO).toContain("ask R4");
    expect(TASKS_HOW_TO).toContain("Research");
  });

  test("Enter/a seeds chat and opens Dashboard", () => {
    expect(handleTaskShortcut("enter")).toBe(true);
    expect(workspace.paneView).toBe("dashboard");
    expect(workspace.pendingChatDraft).toContain("Investigate ML models");
    expect(workspace.pendingChatDraft).toContain("demo08-nl");
  });

  test("a asks about selected task", () => {
    expect(askAboutSelectedTask()).toBe(true);
    expect(workspace.paneView).toBe("dashboard");
    expect(workspace.pendingChatDraft).toMatch(/Help with this research task/);
  });

  test("r jumps to Research", () => {
    expect(handleTaskShortcut("r")).toBe(true);
    expect(workspace.paneView).toBe("research");
  });

  test("k jumps to Knowledge", () => {
    expect(handleTaskShortcut("k")).toBe(true);
    expect(workspace.paneView).toBe("knowledge");
  });

  test("no-ops off tasks space", () => {
    actions.setSpace("dashboard");
    expect(handleTaskShortcut("r")).toBe(false);
  });
});
