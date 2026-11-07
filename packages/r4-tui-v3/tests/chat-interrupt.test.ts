/**
 * Chat interrupt + tool approval store wiring.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("closeAllOverlays chat interrupt", () => {
  it("clears chatStreaming via interruptChat", () => {
    setWorkspace("chatStreaming", true);
    setWorkspace("isTyping", true);
    actions.setChatTurnStatus("tool: research_loop_start");
    actions.closeAllOverlays();
    expect(workspace.chatStreaming).toBe(false);
    expect(workspace.isTyping).toBe(false);
    expect(workspace.chatTurnStatus).toBeNull();
  });
});

describe("tool approval store", () => {
  it("opens and closes approval requests", () => {
    actions.openToolApproval({
      id: 1,
      tool: "research_loop_start",
      description: "run",
      sessionApprovalAllowed: true,
    });
    expect(workspace.toolApproval?.tool).toBe("research_loop_start");
    actions.closeToolApproval();
    expect(workspace.toolApproval).toBeNull();
  });

  it("dispatchToolApprovalKey yes/no requires an open request", async () => {
    const { dispatchToolApprovalKey } = await import("../src/bridge/approval-bridge");
    expect(dispatchToolApprovalKey("y")).toBe(false);
    actions.openToolApproval({
      id: 2,
      tool: "tool_search",
      sessionApprovalAllowed: true,
    });
    expect(dispatchToolApprovalKey("y")).toBe(true);
    expect(workspace.toolApproval).toBeNull();
  });
});
