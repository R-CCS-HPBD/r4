/**
 * Workflow execution progress on screen (plan H-4, `umrs-workflow` drift).
 *
 * The bridge sends `progress` as a 0..1 float (r4_contracts
 * `WorkflowStatus.progress`, UMRS `ExecutionRecord.progress`); the store
 * typed it as a string, so the work pane printed the raw fraction ("0.5"),
 * the exec overlay header did too, and the list overlay's detail line threw
 * (`truncate` called `.slice` on the number). Every surface now shows the
 * percent, as the tool-activity line always did.
 */
import { describe, it, expect, beforeEach, afterEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { WorkRoute } from "../src/routes/work";
import { ListOverlay } from "../src/components/list-overlay";
import { WorkflowExecOverlay } from "../src/components/workflow-exec-overlay";
import { actions, resetWorkspace, setWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { executionRowDetail, setUmrsWorkflowStore, type UmrsWorkflowExecution } from "../src/stores/umrs-workflow";
import { setTeamsState } from "../src/stores/teams";
import { setLoopsState } from "../src/stores/loops";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import type { RpcResult } from "../src/bridge/rpc";

const EXEC = "exec-progress-0001";

/** A workflow.status reply as the bridge sends it (typed by the contract). */
const STATUS: RpcResult<"workflow.status"> = {
  execution_id: EXEC,
  workflow_name: "defect-ai",
  status: "running",
  progress: 0.5,
  error: null,
};

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

function seed(execution: UmrsWorkflowExecution): void {
  setUmrsWorkflowStore("catalog", []);
  setUmrsWorkflowStore("activeExecutions", reconcile({ [execution.execution_id]: execution }));
}

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  resetWorkspace();
  agentActions.resetAgentRuntime();
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  setLoopsState({ rows: [], error: null });
  setWorkspace("paneFocus", true);
  setCompanionBridgeForTests({
    ensureStarted: async () => undefined,
    request: async (method: string) => (method === "workflow.list" ? { workflows: [], count: 0 } : {}),
  } as never);
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  setCompanionBridgeForTests(null);
  actions.setExecDetailId(null);
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  resetWorkspace();
});

describe("executionRowDetail", () => {
  it("shows a fraction as a whole percent, an error first, nothing when absent", () => {
    expect(executionRowDetail(STATUS)).toBe("50%");
    expect(executionRowDetail({ ...STATUS, progress: 0 })).toBe("0%");
    expect(executionRowDetail({ ...STATUS, progress: 1 })).toBe("100%");
    expect(executionRowDetail({ ...STATUS, error: "step train failed" })).toBe("step train failed");
    expect(executionRowDetail({ ...STATUS, progress: null })).toBeNull();
  });
});

describe("progress on screen", () => {
  it("the work pane row shows the percent, not the raw fraction", async () => {
    seed({ ...STATUS, progress: 0.25 });
    const setup = await testRender(() => <WorkRoute />, { width: 120, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("exec-progres · defect-ai · running");
    expect(frame).toContain("25%");
    expect(frame).not.toContain("0.25");
    setup.renderer.destroy();
  });

  it("the list overlay shows the selected execution's percent (it threw on the number)", async () => {
    seed(STATUS);
    const setup = await testRender(() => <ListOverlay />, { width: 120, height: 40 });
    actions.openListOverlay("workflow");
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("defect-ai · running");
    expect(frame).toContain("50%");
    setup.renderer.destroy();
  });

  it("the execution overlay header shows the percent", async () => {
    seed({ ...STATUS, progress: 0.75 });
    actions.setExecDetailId(EXEC);
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("status: running · 75%");
    expect(frame).not.toContain("0.75");
    setup.renderer.destroy();
  });
});
