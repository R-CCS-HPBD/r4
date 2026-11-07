/**
 * TUI audit r2 (turns lane) — bridge-side defects.
 *
 * #19     tool_result carries its full output in data.result, not raw.result.
 * #24     a normal turn end completes the companion agent row (the bridge
 *         emits turn_stopped only on cancel).
 * #25/#40 the resource poller sends no RPC while a turn is in flight on its
 *         bridge (the server parks them and drains them before the reply).
 * #28     a bridge error's recovery hint reaches the rejected Error and the
 *         chat reply.
 * #59     a remote turn ends when the runtime is stopped/failed mid-turn.
 * #26     ask-once approvals are scoped to the companion session.
 * #13     an answered tool approval settles its Research-approvals mirror;
 *         approving the mirror answers the companion.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, agentActions, agentRuntime, resetWorkspace, workspace } from "../src/stores";
import {
  flushTurnSummary,
  onBridgeEvent,
  resetCompanionRuntimeBridgeForTests,
  retargetResourcePolling,
} from "../src/bridge/companion-runtime-bridge";
import {
  BridgeRequestError,
  CompanionBridgeClient,
  bridgeErrorHint,
  setCompanionBridgeForTests,
} from "../src/bridge/jsonl-client";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import type { ChatStreamChunk } from "../src/adapters/types";
import { evaluateRemoteSync } from "../src/lib/venue-runtime";
import {
  answerToolApprovalGate,
  resetApprovalMirrorsForTests,
  handleApprovalRequest,
  respondToolApproval,
} from "../src/bridge/approval-bridge";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  resetWorkspace();
  resetCompanionRuntimeBridgeForTests();
  agentActions.resetAgentRuntime();
  resetApprovalMirrorsForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setCompanionBridgeForTests(null);
});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function collect(stream: AsyncGenerator<ChatStreamChunk>): Promise<ChatStreamChunk[]> {
  const out: ChatStreamChunk[] = [];
  for await (const chunk of stream) out.push(chunk);
  return out;
}

function turnBridge(request: (method: string) => Promise<unknown>) {
  return {
    alive: true,
    ensureStarted: async () => {},
    on: () => {},
    off: () => {},
    request,
    abortInFlightSend: () => {},
    cancelTurn: () => {},
  };
}

describe("r2 #19 — tool_result reads the full output from data.result", () => {
  test("a long workflow error JSON is an error row with the full output", () => {
    const body = JSON.stringify({
      error: "workflow_error",
      detail: "x".repeat(400),
      code: "workflow_validation",
    });
    onBridgeEvent({ type: "tool_call", data: { name: "workflow_run", arguments: {}, tool_call_id: "c1" } });
    onBridgeEvent({
      type: "tool_result",
      message: body.slice(0, 200),
      data: { name: "workflow_run", result: body, tool_call_id: "c1" },
    });
    flushTurnSummary();
    const line = workspace.turnToolActivity[workspace.turnToolActivity.length - 1];
    expect(line?.status).toBe("error");
    expect(line?.resultText).toBe(body);
  });
});

describe("r2 #24 — a normal turn end completes the companion agent row", () => {
  test("send_message resolving (no turn_stopped line) marks the row completed", async () => {
    onBridgeEvent({ type: "tool_call", data: { name: "file_read", arguments: {}, tool_call_id: "c1" } });
    onBridgeEvent({ type: "tool_result", message: "ok", data: { name: "file_read", result: "ok", tool_call_id: "c1" } });
    const row = () => agentRuntime.agents.find((a) => a.role === "companion");
    expect(row()?.state).toBe("waiting");
    const bridge = turnBridge(async () => ({ response: "done", state: { session: { model: "m" } } }));
    await collect(new CompanionChatBackend(bridge as never).chatStream("go", [], "m"));
    expect(row()?.state).toBe("completed");
    expect(row()?.currentTask).toBe("turn complete");
  });
});

describe("r2 #25/#40 / plan H-8 — the resource poller polls during a turn", () => {
  test("the feed RPCs go to a bridge with a pending turn (reads answer mid-turn)", async () => {
    const methods: string[] = [];
    const bridge = {
      alive: true,
      turnInFlight: true,
      request: async (method: string) => {
        methods.push(method);
        return {};
      },
    };
    retargetResourcePolling(bridge as never);
    await sleep(10);
    expect(methods).toEqual(["get_resources", "get_artifacts", "get_science_loop", "get_knowledge"]);
    retargetResourcePolling({ alive: false } as never);
  });

  test("a dead bridge is never polled (no spawn from a poller)", async () => {
    const methods: string[] = [];
    const bridge = {
      alive: false,
      turnInFlight: false,
      request: async (method: string) => {
        methods.push(method);
        return {};
      },
    };
    retargetResourcePolling(bridge as never);
    await sleep(10);
    expect(methods).toEqual([]);
  });

  test("turnInFlight tracks pending turn RPCs on the real client", () => {
    const client = new CompanionBridgeClient();
    const pending = (client as unknown as { pending: Map<number, unknown> }).pending;
    expect(client.turnInFlight).toBe(false);
    pending.set(1, { resolve() {}, reject() {}, timer: null, method: "get_resources" });
    expect(client.turnInFlight).toBe(false);
    pending.set(2, { resolve() {}, reject() {}, timer: null, method: "send_message" });
    expect(client.turnInFlight).toBe(true);
    pending.delete(2);
    pending.set(3, { resolve() {}, reject() {}, timer: null, method: "confirm_plan" });
    expect(client.turnInFlight).toBe(true);
  });
});

describe("r2 #24/#25 / plan H-8 — get_session_plan mid-turn, after the todo tool ran", () => {
  test("a todo_write during a turn refreshes the plan graph on its result", async () => {
    const methods: string[] = [];
    const bridge = {
      alive: true,
      turnInFlight: true,
      request: async (method: string) => {
        methods.push(method);
        return { steps: [] };
      },
    };
    setCompanionBridgeForTests(bridge as never);
    const todos = { todos: [{ content: "step one", status: "pending", priority: "high" }] };
    onBridgeEvent({ type: "tool_call", data: { name: "todowrite", arguments: todos, tool_call_id: "t1" } });
    await sleep(10);
    expect(workspace.sessionTodos).toHaveLength(1);
    expect(methods).not.toContain("get_session_plan"); // the mirror is written while the tool runs

    onBridgeEvent({ type: "tool_result", message: "ok", data: { name: "todowrite", result: "ok", tool_call_id: "t1" } });
    await sleep(10);
    expect(methods).toEqual(["get_session_plan"]);
  });
});

describe("r2 #28 — bridge error hints reach the user", () => {
  test("dispatchResponse keeps error.hint and error.type on the rejection", async () => {
    const client = new CompanionBridgeClient();
    const internals = client as unknown as {
      pending: Map<number, unknown>;
      dispatchResponse: (r: Record<string, unknown>) => void;
    };
    const rejected = new Promise<unknown>((resolve) => {
      internals.pending.set(7, { resolve, reject: resolve, timer: null, method: "send_message" });
    });
    internals.dispatchResponse({
      id: 7,
      ok: false,
      error: { message: "401 Unauthorized", hint: "Auth stale — /health then /login", type: "AuthenticationError" },
    });
    const err = await rejected;
    expect(err).toBeInstanceOf(BridgeRequestError);
    expect((err as Error).message).toBe("401 Unauthorized");
    expect(bridgeErrorHint(err)).toBe("Auth stale — /health then /login");
    expect((err as BridgeRequestError).errorType).toBe("AuthenticationError");
  });

  test("a hinted send failure shows the server's next step, not 'check companion is installed'", async () => {
    const bridge = turnBridge(async (method) => {
      if (method === "send_message") {
        throw new BridgeRequestError("APIError 404: model not found", "/endpoints or /models", "APIError");
      }
      return {};
    });
    const chunks = await collect(new CompanionChatBackend(bridge as never).chatStream("go", [], undefined));
    const text = chunks.map((c) => c.delta ?? "").join("");
    expect(text).toContain("model not found");
    expect(text).toContain("/endpoints or /models");
    expect(text).not.toContain("Check companion is installed");
    expect(text.startsWith("[Chat unavailable]")).toBe(true);
  });
});

describe("r2 #59 — a stopped/failed runtime ends the remote turn", () => {
  test("venue_state stopped with a stale running phase ends the turn as an error", () => {
    for (const state of ["stopped", "failed"]) {
      const step = evaluateRemoteSync(
        { venue_state: state, runtime: { phase: "running", detail: "lease expired" }, state: { total_messages: 1 } },
        1,
        "",
      );
      expect(step.done).toBe(true);
      expect(step.phase).toBe("error");
      expect(step.errorDetail).toContain(`remote runtime ${state}`);
      expect(step.errorDetail).toContain("lease expired");
    }
  });

  test("paused and running rows keep the turn alive; a finished turn keeps its answer", () => {
    for (const state of ["running", "paused"]) {
      const step = evaluateRemoteSync({ venue_state: state, runtime: { phase: "running" } }, 1, "");
      expect(step.done).toBe(false);
    }
    const finished = evaluateRemoteSync(
      {
        adopted: true,
        venue_state: "stopped",
        runtime: { phase: "idle" },
        state: { total_messages: 3, messages: [{ role: "assistant", content: "answer" }] },
      },
      1,
      "",
    );
    expect(finished.done).toBe(true);
    expect(finished.phase).toBe("idle");
    expect(finished.finalText).toBe("answer");
  });
});

// r2 #26 (ask-once scoped per companion session) retired with ask-once
// itself (ADR-104 D-3): the companion remembers, the console never answers.
// tests/adr104-approval-cards.test.tsx pins "a repeat ask shows a card".

describe("r2 #13 — the Research-approvals mirror follows the card", () => {
  function fakeApprovalBridge() {
    const answers: Array<[string | number, string]> = [];
    return {
      answers,
      respondApproval: (id: string | number, decision: string) => answers.push([id, decision]),
    };
  }

  test("answering the card settles its mirrored gate", () => {
    const bridge = fakeApprovalBridge();
    setCompanionBridgeForTests(bridge as never);
    handleApprovalRequest({ id: 7, tool: "file_write", session_approval_allowed: true });
    const gate = () => agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:7");
    expect(gate()?.status).toBe("pending");
    respondToolApproval("once");
    expect(bridge.answers).toEqual([[7, "once"]]);
    expect(gate()?.status).toBe("approved");

    handleApprovalRequest({ id: 8, tool: "file_write", session_approval_allowed: false });
    respondToolApproval("reject");
    expect(agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:8")?.status).toBe("rejected");
  });

  test("approving the mirror of the open card answers the companion", () => {
    const bridge = fakeApprovalBridge();
    setCompanionBridgeForTests(bridge as never);
    handleApprovalRequest({ id: 9, tool: "file_write", session_approval_allowed: true });
    const gateId = agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:9")!.id;
    expect(answerToolApprovalGate(gateId, "yes")).toBe(true);
    expect(bridge.answers).toEqual([[9, "once"]]);
    expect(workspace.toolApproval).toBeNull();
    expect(agentRuntime.approvalGates.find((g) => g.id === gateId)?.status).toBe("approved");
  });

  test("non-mirror gates are left to their owners", () => {
    const id = agentActions.requestApproval("experiment_execution", "Run X", undefined, undefined, "experiment:1");
    expect(answerToolApprovalGate(id, "yes")).toBe(false);
    expect(agentRuntime.approvalGates.find((g) => g.id === id)?.status).toBe("pending");
  });
});
