/**
 * TUI audit r2 (2026-10-17), hooks lane — findings 14/42/41: the execution
 * overlay and the live graph read `steps`, which only workflow.detail
 * carries; the live poll must survive a chat turn instead of stopping for good.
 * Plan H-8 (2026-10-18): the bridge answers reads mid-turn, so the poll
 * keeps polling during a turn (it used to step aside).
 */
import { afterAll, beforeEach, describe, expect, it } from "bun:test";
import { actions, setWorkspace } from "../src/stores";
import {
  _clearExecutionPollsForTests,
  _fireExecutionPollForTests,
  _hasExecutionPollForTests,
  setUmrsWorkflowStore,
  umrsWorkflowActions,
  umrsWorkflowStore,
} from "../src/stores/umrs-workflow";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  setCompanionBridgeForTests(null);
  _clearExecutionPollsForTests();
});

const EXEC = "exec-r2-0001";
const calls: string[] = [];
let reply: (method: string) => unknown = () => ({});

const bridge = {
  ensureStarted: async () => undefined,
  request: async (method: string) => {
    calls.push(method);
    const value = reply(method);
    if (value instanceof Error) throw value;
    return value;
  },
} as unknown as CompanionBridgeClient;

const detail = (status: string) => ({
  execution: {
    execution_id: EXEC,
    workflow_name: "wf",
    status,
    steps: [
      { name: "prep", status: "completed" },
      { name: "train", status },
    ],
  },
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  actions.resetWorkspace();
  _clearExecutionPollsForTests();
  setUmrsWorkflowStore({ catalog: [], activeExecutions: {}, lastError: null, loading: false });
  setCompanionBridgeForTests(bridge);
  calls.length = 0;
  reply = (method) => (method === "workflow.detail" ? detail("running") : { execution_id: EXEC, status: "running" });
});

describe("execution refresh carries step rows", () => {
  it("refreshStatus reads workflow.detail and stores its steps", async () => {
    await umrsWorkflowActions.refreshStatus(EXEC);
    // A live run's definition is read once too (2026-10-24, the badge's
    // step total) — pinned in workflow-graph-verb.test.tsx.
    expect(calls.filter((m) => m !== "workflow.package")).toEqual(["workflow.detail"]);
    const stored = umrsWorkflowStore.activeExecutions[EXEC] as unknown as { steps?: unknown[] };
    expect(stored.steps?.length).toBe(2);
    expect(umrsWorkflowStore.activeExecutions[EXEC]!.status).toBe("running");
  });

  it("the live poll moves the step rows with the status", async () => {
    await umrsWorkflowActions.refreshStatus(EXEC);
    reply = () => detail("completed");
    await _fireExecutionPollForTests(EXEC);
    const stored = umrsWorkflowStore.activeExecutions[EXEC] as unknown as {
      status: string;
      steps: Array<{ status: string }>;
    };
    expect(stored.status).toBe("completed");
    expect(stored.steps[1]!.status).toBe("completed");
    expect(_hasExecutionPollForTests(EXEC)).toBe(false);
  });
});

describe("live execution poll survives a chat turn", () => {
  it("keeps polling while a chat turn streams and stays armed (plan H-8)", async () => {
    await umrsWorkflowActions.refreshStatus(EXEC);
    calls.length = 0;
    setWorkspace("chatStreaming", true);
    await _fireExecutionPollForTests(EXEC);
    expect(calls).toEqual(["workflow.detail"]);
    expect(_hasExecutionPollForTests(EXEC)).toBe(true);
  });

  it("re-arms after a timed-out poll instead of stopping for good", async () => {
    await umrsWorkflowActions.refreshStatus(EXEC);
    reply = () => new Error("bridge request timed out: workflow.detail");
    await _fireExecutionPollForTests(EXEC);
    expect(_hasExecutionPollForTests(EXEC)).toBe(true);
    expect(umrsWorkflowStore.activeExecutions[EXEC]!.status).toBe("running");
  });

  it("stops on a not-found answer", async () => {
    await umrsWorkflowActions.refreshStatus(EXEC);
    reply = () => new Error("execution not found: exec-r2-0001");
    await _fireExecutionPollForTests(EXEC);
    expect(_hasExecutionPollForTests(EXEC)).toBe(false);
  });
});
