import { describe, expect, it, beforeEach } from "bun:test";
import { actions, workspace, setWorkspace } from "../src/stores";
import { letterShortcutsBlocked } from "../src/lib/space-shortcuts";
import { wrapText } from "../src/lib/text";

describe("ask_user overlay state", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    actions.closeAllOverlays();
  });

  it("openAskUser blocks letter shortcuts until closed", () => {
    expect(letterShortcutsBlocked()).toBe(false);
    actions.openAskUser({
      id: 7,
      question: "Which path should we use?",
      options: ["/mnt/c/a", "/mnt/c/b"],
    });
    expect(workspace.askUser?.id).toBe(7);
    expect(letterShortcutsBlocked()).toBe(true);
    actions.closeAskUser();
    expect(workspace.askUser).toBeNull();
    expect(letterShortcutsBlocked()).toBe(false);
  });

  it("interruptChat clears askUser and turn status", () => {
    actions.openAskUser({ id: 1, question: "Continue?", options: [] });
    setWorkspace("chatStreaming", true);
    actions.setChatTurnStatus("Waiting for your answer…");
    actions.interruptChat();
    expect(workspace.askUser).toBeNull();
    expect(workspace.chatStreaming).toBe(false);
    expect(workspace.chatTurnStatus).toBeNull();
  });
});

describe("wrapText for Context panel", () => {
  it("wraps long task titles without hard truncation ellipsis", () => {
    const title =
      "Design and execute experiments to verify hypotheses about material properties";
    const lines = wrapText(title, 24, 6);
    expect(lines.length).toBeGreaterThan(1);
    expect(lines.join(" ")).toContain("Design and execute");
    expect(lines.every((l) => l.length <= 24)).toBe(true);
  });
});
