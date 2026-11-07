/**
 * Workflow manual-approval gate (W5) — the bridge served workflow.approve_gate
 * since the W5 wave but NOTHING in the console called it: no slash verb, no
 * overlay key. These tests pin the wiring: [a] on a workflow execution row
 * discovers the waiting_approval step from live detail and submits the
 * decision.
 */

import { describe, it, expect, beforeEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { workspace, actions } from "../src/stores";
import { ListOverlay } from "../src/components/list-overlay";
import {
  umrsWorkflowActions,
  umrsWorkflowStore,
  setUmrsWorkflowStore,
} from "../src/stores/umrs-workflow";
import { waitingApprovalStepName } from "../src/lib/workflow-graph-projection";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import type { RpcMethod, RpcResult } from "../src/bridge/rpc";

// Isolation: several suites pin R4_TUI_BACKEND=offline / demo mode at module
// scope and never restore it; file order in the runner is not a contract.
// These tests need a LIVE backend posture, so they take it explicitly.
const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

/** Replies typed by the bridge contract (plan H-4.5): a fake answers the wire shape. */
type FakeReplies = { [M in RpcMethod]?: RpcResult<M> | Error };

function fakeBridge(replies: FakeReplies) {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  return {
    calls,
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      const reply = replies[method as RpcMethod];
      if (reply instanceof Error) throw reply;
      return reply ?? {};
    },
  };
}

const EXEC_ID = "exec-0123456789abcdef";

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  actions.resetWorkspace();
  setUmrsWorkflowStore({ catalog: [], activeExecutions: {}, lastError: null, loading: false });
});

describe("waitingApprovalStepName", () => {
  it("finds the waiting_approval step in the results.steps map shape", () => {
    const exec = {
      execution_id: EXEC_ID,
      status: "running",
      results: {
        steps: {
          ingest: { status: "completed" },
          review_gate: { status: "waiting_approval" },
          publish: { status: "pending" },
        },
      },
    } as never;
    expect(waitingApprovalStepName(exec)).toBe("review_gate");
  });

  it("finds the waiting_approval step in the array shape", () => {
    const exec = {
      execution_id: EXEC_ID,
      status: "running",
      results: {
        steps: [
          { name: "ingest", status: "completed" },
          { name: "gate1", status: "waiting_approval" },
        ],
      },
    } as never;
    expect(waitingApprovalStepName(exec)).toBe("gate1");
  });

  it("returns null when no step waits (running / terminal executions)", () => {
    expect(
      waitingApprovalStepName({
        execution_id: EXEC_ID,
        status: "completed",
        results: { steps: { a: { status: "completed" } } },
      } as never),
    ).toBeNull();
    expect(waitingApprovalStepName({ execution_id: EXEC_ID, status: "running" } as never)).toBeNull();
  });
});

describe("umrsWorkflowActions.approveGate", () => {
  it("sends workflow.approve_gate with execution_id + step_name and tracks the result", async () => {
    const bridge = fakeBridge({
      "workflow.approve_gate": {
        execution_id: EXEC_ID,
        workflow_name: "example-pipeline",
        status: "running",
        progress: null,
        error: null,
        ok: true,
      },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const exec = await umrsWorkflowActions.approveGate(EXEC_ID, "review_gate");
      // The live run's one-time definition read (2026-10-24) is not this pin.
      const calls = bridge.calls.filter((c: { method: string }) => c.method !== "workflow.package");
      expect(calls).toEqual([
        {
          method: "workflow.approve_gate",
          params: { execution_id: EXEC_ID, step_name: "review_gate", approved: true },
        },
      ]);
      expect(exec.status).toBe("running");
      expect(umrsWorkflowStore.activeExecutions[EXEC_ID]?.status).toBe("running");
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});

describe("workflow overlay [a] approve-gate", () => {
  it("discovers the waiting step from detail and approves it", async () => {
    setUmrsWorkflowStore("activeExecutions", EXEC_ID, {
      execution_id: EXEC_ID,
      workflow_name: "example-pipeline",
      status: "running",
    });
    const bridge = fakeBridge({
      "workflow.list": { workflows: [], count: 0 },
      "workflow.detail": {
        execution: {
          execution_id: EXEC_ID,
          workflow_name: "example-pipeline",
          status: "running",
          results: {
            steps: {
              ingest: { status: "completed" },
              review_gate: { status: "waiting_approval" },
            },
          },
        },
      },
      "workflow.approve_gate": {
        execution_id: EXEC_ID,
        workflow_name: "example-pipeline",
        status: "running",
        progress: null,
        error: null,
        ok: true,
      },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const setup = await testRender(() => <ListOverlay />, { width: 120, height: 40 });
      actions.openListOverlay("workflow");
      await setup.renderOnce();
      // Cursor starts on the first row — the seeded execution.
      setup.mockInput.pressKey("a" as never);
      await setup.renderOnce();
      await new Promise((r) => setTimeout(r, 100));
      await setup.renderOnce();
      const methods = bridge.calls.map((c) => c.method);
      expect(methods).toContain("workflow.detail");
      expect(methods).toContain("workflow.approve_gate");
      const approve = bridge.calls.find((c) => c.method === "workflow.approve_gate")!;
      expect(approve.params).toEqual({
        execution_id: EXEC_ID,
        step_name: "review_gate",
        approved: true,
      });
      const notice = workspace.chatMessages.at(-1)!.content;
      expect(notice).toContain("approved gate review_gate");
      setup.renderer.destroy();
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  it("reports honestly when no gate waits", async () => {
    setUmrsWorkflowStore("activeExecutions", EXEC_ID, {
      execution_id: EXEC_ID,
      workflow_name: "example-pipeline",
      status: "running",
    });
    const bridge = fakeBridge({
      "workflow.list": { workflows: [], count: 0 },
      "workflow.detail": {
        execution: {
          execution_id: EXEC_ID,
          workflow_name: "example-pipeline",
          status: "running",
          results: { steps: { ingest: { status: "running" } } },
        },
      },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const setup = await testRender(() => <ListOverlay />, { width: 120, height: 40 });
      actions.openListOverlay("workflow");
      await setup.renderOnce();
      setup.mockInput.pressKey("a" as never);
      await setup.renderOnce();
      await new Promise((r) => setTimeout(r, 100));
      await setup.renderOnce();
      expect(bridge.calls.map((c) => c.method)).not.toContain("workflow.approve_gate");
      const notice = workspace.chatMessages.at(-1)!.content;
      expect(notice).toContain("no gate waiting for approval");
      setup.renderer.destroy();
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});
