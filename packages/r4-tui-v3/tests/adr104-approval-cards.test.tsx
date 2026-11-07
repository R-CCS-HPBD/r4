/**
 * ADR-104 D-3: the companion answers, the console renders.
 *
 * Each case fails on the pre-ADR-104 console: it answered a repeat ask of a
 * tool itself (ask-once), replaced an open card with a second ask, rejected
 * the pending approval on Esc of ANY modal layer, hid `a` from the legend,
 * and wrote a rule file + sent "yes" for `p`.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import {
  answerToolApprovalGate,
  dispatchToolApprovalKey,
  handleApprovalRequest,
  resetApprovalMirrorsForTests,
  respondToolApproval,
} from "../src/bridge/approval-bridge";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { runClaim } from "../src/keymap/key-claims";
import { TURN_TRANSIENT_FIELDS } from "../src/tabs/chat-tab-store";
import { approvalOptionsOf, decisionForKey } from "../src/lib/approval-options";
import "../src/keymap/store-claims";

type Answer = { id: unknown; decision: string; extra?: unknown };

function fakeBridge() {
  const answered: Answer[] = [];
  return {
    answered,
    request: async () => ({}),
    respondApproval: (id: unknown, decision: string, extra?: unknown) => {
      answered.push(extra === undefined ? { id, decision } : { id, decision, extra });
    },
    respondAskUser: () => undefined,
    on: () => undefined,
  };
}

let bridge = fakeBridge();

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  resetApprovalMirrorsForTests();
  agentActions.resetAgentRuntime();
  setWorkspace("project", { id: "proj-103", name: "t", description: "", status: "active", goalIds: [] });
  bridge = fakeBridge();
  setCompanionBridgeForTests(bridge as never);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
  agentActions.resetAgentRuntime();
});

const ask = (id: number, tool = "shell_exec", extra: Record<string, unknown> = {}) => ({
  id,
  tool,
  description: `call ${id}`,
  session_approval_allowed: true,
  ...extra,
});

describe("no auto-answer (defect 5)", () => {
  it("a second ask of the same tool after `once` shows a card", () => {
    handleApprovalRequest(ask(1, "shell_exec", { description: "ls" }));
    expect(dispatchToolApprovalKey("y")).toBe(true);
    handleApprovalRequest(ask(2, "shell_exec", { description: "rm -rf x" }));
    expect(workspace.toolApproval?.id).toBe(2);
    expect(bridge.answered).toEqual([{ id: 1, decision: "once" }]);
  });

  it("after `always` a repeat ask the companion still sends shows a card too", () => {
    handleApprovalRequest(ask(1));
    expect(dispatchToolApprovalKey("a")).toBe(true);
    handleApprovalRequest(ask(2));
    expect(workspace.toolApproval?.id).toBe(2);
    expect(bridge.answered).toEqual([{ id: 1, decision: "always" }]);
  });
});

describe("queued cards (defect 10)", () => {
  it("two asks queue; answering the first shows the second", () => {
    handleApprovalRequest(ask(1, "shell_exec"));
    handleApprovalRequest(ask(2, "file_write", { diff: "--- a\n+++ b\n" }));
    expect(workspace.toolApproval?.id).toBe(1);
    expect(workspace.toolApprovalQueue.map((c) => c.id)).toEqual([2]);
    respondToolApproval("reject");
    expect(workspace.toolApproval?.id).toBe(2);
    expect(workspace.toolApproval?.tool).toBe("file_write");
    expect(workspace.toolApprovalQueue).toEqual([]);
    respondToolApproval("once");
    expect(workspace.toolApproval).toBeNull();
    expect(bridge.answered).toEqual([
      { id: 1, decision: "reject" },
      { id: 2, decision: "once" },
    ]);
  });

  it("the next card does not inherit the answered card's diff", () => {
    handleApprovalRequest(ask(1, "file_write", { diff: "--- a\n+++ b\n", path: "a.txt" }));
    handleApprovalRequest(ask(2, "shell_exec"));
    respondToolApproval("once");
    expect(workspace.toolApproval?.id).toBe(2);
    expect(workspace.toolApproval?.diff).toBeUndefined();
    expect(workspace.toolApproval?.path).toBeUndefined();
  });

  it("the same id twice is one ask", () => {
    handleApprovalRequest(ask(1));
    handleApprovalRequest(ask(1));
    expect(workspace.toolApprovalQueue).toEqual([]);
  });

  it("a queued card answered from the Research list answers the companion", () => {
    handleApprovalRequest(ask(1));
    handleApprovalRequest(ask(2));
    const gate = agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:2");
    expect(gate).toBeDefined();
    expect(answerToolApprovalGate(gate!.id, "no")).toBe(true);
    expect(bridge.answered).toEqual([{ id: 2, decision: "reject" }]);
    expect(workspace.toolApproval?.id).toBe(1);
    expect(workspace.toolApprovalQueue).toEqual([]);
  });

  it("a closed tab drops its queued asks with the card", () => {
    expect(TURN_TRANSIENT_FIELDS).toContain("toolApproval");
    expect(TURN_TRANSIENT_FIELDS).toContain("toolApprovalQueue");
  });
});

describe("Esc (defect 10)", () => {
  it("Esc on the help overlay leaves the approval pending; Esc on the card rejects it", () => {
    handleApprovalRequest(ask(1));
    actions.toggleHelp();
    expect(workspace.helpOpen).toBe(true);
    expect(runClaim("escape")).toBe(true);
    expect(workspace.helpOpen).toBe(false);
    expect(workspace.toolApproval?.id).toBe(1);
    expect(bridge.answered).toEqual([]);

    expect(runClaim("escape")).toBe(true);
    expect(workspace.toolApproval).toBeNull();
    expect(bridge.answered).toEqual([{ id: 1, decision: "reject" }]);
  });

  it("the card's Esc settles its Research-list mirror", () => {
    handleApprovalRequest(ask(3));
    const gate = agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:3");
    expect(gate?.status).toBe("pending");
    runClaim("escape");
    const after = agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:3");
    expect(after?.status).toBe("rejected");
  });
});

describe("options (D-3)", () => {
  it("options [once, reject] hide a/p and `a` does nothing", async () => {
    handleApprovalRequest(ask(1, "shell_exec", { options: ["once", "reject"] }));
    expect(workspace.toolApproval?.options).toEqual(["once", "reject"]);
    expect(dispatchToolApprovalKey("a")).toBe(false);
    expect(dispatchToolApprovalKey("p")).toBe(false);
    expect(workspace.toolApproval?.id).toBe(1);
    expect(bridge.answered).toEqual([]);

    const { ToolApprovalOverlay } = await import("../src/components/tool-approval-overlay");
    const setup = await testRender(() => <ToolApprovalOverlay />, { width: 160, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("y/Enter allow once");
    expect(frame).not.toContain("a always this session");
    expect(frame).not.toContain("p always this project");
    setup.renderer.destroy();
  });

  it("an ordinary card shows a, and never a project answer (amendment 1)", async () => {
    // an older bridge may still list always_project: the card ignores it
    handleApprovalRequest(ask(1, "shell_exec", { options: ["once", "always", "always_project", "reject"] }));
    const { ToolApprovalOverlay } = await import("../src/components/tool-approval-overlay");
    const setup = await testRender(() => <ToolApprovalOverlay />, { width: 200, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("a always this session");
    expect(frame).toContain("[A]lways");
    expect(frame).not.toContain("always this project");
    expect(frame).not.toContain("[P]roject");
    setup.renderer.destroy();
  });

  it("`p` is not an answer key any more; nothing is sent (ADR-104 amendment 1)", () => {
    const requests: string[] = [];
    const b = { ...fakeBridge(), request: async (m: string) => (requests.push(m), {}) };
    setCompanionBridgeForTests(b as never);
    handleApprovalRequest(ask(7));
    expect(dispatchToolApprovalKey("p")).toBe(false);
    expect(b.answered).toEqual([]);
    expect(requests).not.toContain("trust_grant");
    expect(workspace.toolApproval?.id).toBe(7);
  });

  it("an older bridge (no options) derives them from session_approval_allowed / risk tier", () => {
    expect(approvalOptionsOf({ sessionApprovalAllowed: true })).toEqual(["once", "always", "reject"]);
    expect(approvalOptionsOf({ sessionApprovalAllowed: false })).toEqual(["once", "reject"]);
    expect(approvalOptionsOf({ neverAuto: true })).toEqual(["once", "reject"]);
    expect(approvalOptionsOf({ fenced: true })).toEqual(["once", "reject"]);
    handleApprovalRequest(ask(1, "knowledge_graph_decide", { risk_tier: "never_auto" }));
    expect(workspace.toolApproval?.options).toEqual(["once", "reject"]);
  });

  it("keys map to the wire words", () => {
    const all = approvalOptionsOf({});
    expect(["y", "return", "a", "p", "r", "escape", "n"].map((k) => decisionForKey(k, all))).toEqual([
      "once",
      "once",
      "always",
      null,
      "reject",
      "reject",
      null,
    ]);
  });
});
