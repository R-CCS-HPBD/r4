/**
 * TUI audit r2 (turns lane) — review follow-ups.
 *
 * #11/#62 a plan turn that streams while its tab is in the background keeps
 *         each round's tool lines under that round (the chat stream's
 *         replay rule, #33) and leaves nothing dangling for the next turn;
 *         its end settles a Ctrl+B task and the fallback flag like a chat
 *         turn's finish().
 * #36     the chat adapter emits `turn_settled` when the turn RPC answers,
 *         so a background tab's busy glyph settles without a visit.
 * #24     the Plan graph follows a todo update mid-turn: since plan H-8 the
 *         bridge answers get_session_plan during a turn, so the todo tool's
 *         result fetches it at once (the refresh used to be owed until the
 *         first idle poll round).
 * #13     approving the mirror of a card still open in a BACKGROUND tab
 *         does not settle the gate behind that tab's companion.
 */
import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, agentActions, agentRuntime, resetWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests, routeTabEvent } from "../src/tabs/registry";
import { wireChatTabsOnce } from "../src/tabs/wiring";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { respondPlan } from "../src/bridge/plan-bridge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import {
  onBridgeEvent,
  resetCompanionRuntimeBridgeForTests,
  retargetResourcePolling,
} from "../src/bridge/companion-runtime-bridge";
import {
  answerToolApprovalGate,
  resetApprovalMirrorsForTests,
  handleApprovalRequest,
  respondToolApproval,
} from "../src/bridge/approval-bridge";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A channel-aware fake bridge (a real EventEmitter, like CompanionBridgeClient). */
class FakeBridge extends EventEmitter {
  alive = true;
  turnInFlight = false;
  readonly methods: string[] = [];
  readonly answers: Array<[string | number, string]> = [];
  private resolvers = new Map<string, (value: unknown) => void>();

  async ensureStarted(): Promise<void> {}
  abortInFlightSend(): void {}
  cancelTurn(): void {}
  respondApproval(id: string | number, decision: string): void {
    this.answers.push([id, decision]);
  }
  request(method: string): Promise<unknown> {
    this.methods.push(method);
    if (method === "send_message" || method === "confirm_plan") {
      return new Promise((resolve) => this.resolvers.set(method, resolve));
    }
    return Promise.resolve({ steps: [] });
  }
  resolve(method: string, value: unknown): void {
    this.resolvers.get(method)?.(value);
  }
}

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  resetCompanionRuntimeBridgeForTests();
  agentActions.resetAgentRuntime();
  resetApprovalMirrorsForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setCompanionBridgeForTests(null);
  retargetResourcePolling({ alive: false } as never);
  resetWorkspace();
  resetTabsRegistryForTests();
});

/** Tab A's bridge as the tab attacher wires it: the tab router listens first. */
function wireTabBridge(bridge: FakeBridge, tabId: string): void {
  for (const channel of ["event", "turn_settled"]) {
    bridge.on(channel, (payload: unknown) => routeTabEvent(tabId, channel, payload));
  }
}

function assistantSegments(): Array<{ text: string; tools: string[] }> {
  return workspace.chatMessages
    .filter((m) => m.role === "assistant" && m.content.startsWith("R"))
    .map((m) => ({ text: m.content, tools: (m.toolCalls ?? []).map((t) => t.text) }));
}

describe("r2 #11/#62 — a plan turn across a tab switch", () => {
  test("each round keeps its own tool lines; nothing dangles for the next turn", async () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    wireTabBridge(bridge, tabA);
    setCompanionBridgeForTests(bridge as never);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.setInteractionModeState({ planAwaitingConfirmation: true });
    const ev = (payload: Record<string, unknown>) => bridge.emit("event", payload);

    const done = respondPlan("execute_auto");
    await sleep(5);
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "R1 text" });
    await sleep(5);
    actions.tabCreate();
    ev({ type: "tool_call", data: { name: "file_read", arguments: { path: "a.txt" }, tool_call_id: "c1" } });
    ev({ type: "tool_result", message: "ok", data: { name: "file_read", result: "ok", tool_call_id: "c1" } });
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "R2 text" });
    ev({ type: "tool_call", data: { name: "shell_exec", arguments: { command: "ls" }, tool_call_id: "c2" } });
    ev({ type: "tool_result", message: "ok", data: { name: "shell_exec", result: "ok", tool_call_id: "c2" } });
    ev({ type: "response_start" });
    bridge.resolve("confirm_plan", { response: "R1 textR2 text", state: {} });
    await sleep(20);
    actions.tabActivate(tabA);
    await done;
    await sleep(30);

    const segs = assistantSegments();
    expect(segs.map((s) => s.text)).toEqual(["R1 text", "R2 text"]);
    expect(segs[0]!.tools.join("\n")).toContain("a.txt");
    expect(segs[1]!.tools.join("\n")).toContain("ls");
    expect(segs[1]!.tools.join("\n")).not.toContain("a.txt");
    expect(workspace.turnToolActivity).toEqual([]);
  });

  test("Ctrl+B during plan execution settles its task when the plan ends", async () => {
    const bridge = new FakeBridge();
    setCompanionBridgeForTests(bridge as never);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.setInteractionModeState({ planAwaitingConfirmation: true });

    const done = respondPlan("execute_auto");
    await sleep(5);
    expect(actions.backgroundCurrentTurn()).toBe(true);
    const taskId = workspace.backgroundTurnTaskId!;
    expect(taskId).toBeTruthy();
    bridge.emit("event", { type: "response_start" });
    bridge.emit("event", { type: "response_chunk", message: "Plan executed." });
    await sleep(5);
    bridge.resolve("confirm_plan", { response: "Plan executed.", state: {} });
    await done;

    expect(workspace.backgroundTurnTaskId).toBeNull();
    expect(workspace.backgroundTasks.find((t) => t.id === taskId)?.status).toBe("done");
    expect(workspace.chatFallbackActive).toBe(false);
  });

  test("a failed plan turn raises the chat fallback flag", async () => {
    const bridge = new FakeBridge();
    bridge.request = (method: string) => {
      bridge.methods.push(method);
      return Promise.reject(new Error("model endpoint down"));
    };
    setCompanionBridgeForTests(bridge as never);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.setInteractionModeState({ planAwaitingConfirmation: true });

    expect(await respondPlan("execute_ask")).toBe(false);
    expect(workspace.chatFallbackActive).toBe(true);
    const last = workspace.chatMessages[workspace.chatMessages.length - 1];
    expect(last?.fallback).toBe(true);
  });
});

describe("r2 #36 — the chat adapter emits turn_settled", () => {
  test("a background tab's busy marker clears when its turn answers", async () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    wireTabBridge(bridge, tabA);
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.sendChatMessage("long work");
    await sleep(10);
    expect(workspace.chatTabs.find((t) => t.id === tabA)?.busy).toBe(true);

    const tabB = actions.tabCreate()!;
    expect(workspace.activeChatTabId).toBe(tabB);
    bridge.resolve("send_message", { response: "done", state: {} });
    await sleep(10);
    expect(workspace.activeChatTabId).toBe(tabB);
    expect(workspace.chatTabs.find((t) => t.id === tabA)?.busy).toBe(false);
  });
});

describe("r2 #24 / plan H-8 — the Plan graph follows a todo update mid-turn", () => {
  test("a slash-dispatched turn's todo result fetches the plan while the turn runs", async () => {
    const bridge = new FakeBridge();
    bridge.turnInFlight = true;
    setCompanionBridgeForTests(bridge as never);
    const todos = { todos: [{ content: "step one", status: "pending", priority: "high" }] };
    onBridgeEvent({ type: "tool_call", data: { name: "todowrite", arguments: todos, tool_call_id: "t1" } });
    await sleep(10);
    // The call has not run yet: the bridge mirrors the list while the tool runs.
    expect(bridge.methods).not.toContain("get_session_plan");

    onBridgeEvent({ type: "tool_result", message: "ok", data: { name: "todowrite", result: "ok", tool_call_id: "t1" } });
    await sleep(10);
    expect(bridge.methods.filter((m) => m === "get_session_plan")).toHaveLength(1);
    expect(bridge.turnInFlight).toBe(true);
  });
});

describe("r2 #13 — a card open in a background tab is answered there only", () => {
  test("approving its mirror from another tab leaves the gate and the companion untouched", () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    setCompanionBridgeForTests(bridge as never);
    handleApprovalRequest({ id: 11, tool: "file_write", session_approval_allowed: true });
    const gate = () => agentRuntime.approvalGates.find((g) => g.objectId === "tool-approval:11");
    const gateId = gate()!.id;

    actions.tabCreate();
    expect(answerToolApprovalGate(gateId, "yes")).toBe(true);
    expect(gate()?.status).toBe("pending");
    expect(bridge.answers).toEqual([]);
    expect(workspace.chatMessages.some((m) => m.content.includes("still open in chat tab"))).toBe(true);

    actions.tabActivate(tabA);
    expect(workspace.toolApproval?.id).toBe(11);
    respondToolApproval("reject");
    expect(bridge.answers).toEqual([[11, "reject"]]);
    expect(gate()?.status).toBe("rejected");
  });
});
