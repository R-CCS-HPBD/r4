/**
 * Reject-with-feedback really sends the feedback (stabilization sweep
 * 2026-10-21, chat#8): the notice said "Feedback queued for the assistant"
 * while the text only sat in the composer and the turn went on without it.
 */

import { afterEach, describe, expect, spyOn, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { ToolApprovalOverlay } from "../src/components/tool-approval-overlay";
import * as approvalBridge from "../src/bridge/approval-bridge";

afterEach(() => {
  actions.closeToolApproval();
  resetWorkspace();
});

async function rejectWith(feedback: string) {
  const sent: string[] = [];
  const spy = spyOn(approvalBridge, "respondToolApproval").mockImplementation((d) => {
    sent.push(d);
    actions.closeToolApproval();
    return true;
  });
  actions.openToolApproval({ id: 9, tool: "shell_exec", description: "Run shell command: rm -rf x", sessionApprovalAllowed: true });
  const setup = await testRender(() => <ToolApprovalOverlay />, { width: 100, height: 40 });
  try {
    await setup.renderOnce();
    setup.mockInput.pressKey("n");
    await setup.renderOnce();
    await setup.mockInput.typeText(feedback);
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();
  } finally {
    spy.mockRestore();
    setup.renderer.destroy();
  }
  return sent;
}

const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);

describe("reject with feedback", () => {
  test("mid-turn: the feedback is queued as the next user message", async () => {
    setWorkspace("chatStreaming", true);
    const sent = await rejectWith("use list_dir instead");
    expect(sent).toEqual(["reject"]);
    expect(workspace.pendingChatQueue.length).toBe(1);
    const queued = workspace.chatMessages.filter((m) => m.role === "user" && m.queued);
    expect(queued.map((m) => m.content)).toEqual(["use list_dir instead"]);
    expect(notices().some((n) => n.includes("queued as your next message"))).toBe(true);
    expect(notices().some((n) => n.includes("Feedback queued for the assistant"))).toBe(false);
  });

  test("no turn running: the composer holds it and the notice says it is not sent", async () => {
    const sent = await rejectWith("use list_dir instead");
    expect(sent).toEqual(["reject"]);
    expect(workspace.pendingChatDraft).toBe("use list_dir instead");
    expect(notices().some((n) => n.includes("not sent yet"))).toBe(true);
  });
});
