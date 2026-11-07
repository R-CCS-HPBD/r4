/**
 * TUI audit r2 (2026-10-17), tabs lane — what a BACKGROUND tab does.
 *
 * #33 replaying a background tab's buffered rounds kept only the last
 *     round's tool lines, pinned under the first text segment;
 * #36 a background turn that ended kept its busy ● and Ctrl+B task until
 *     the tab was visited;
 * #27/#37/#38 session-shaped fields (wf:on, Plan graph, /changes, tools
 *     chip, science loop, knowledge base) leaked across tabs;
 * #38 a fresh tab left the 5 s resource poller on the previous tab's bridge.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { getRuntime, resetTabsRegistryForTests, routeTabEvent } from "../src/tabs/registry";
import { wireChatTabsOnce } from "../src/tabs/wiring";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { flushTurnSummary, retargetResourcePolling } from "../src/bridge/companion-runtime-bridge";
import { setDemoMode } from "../src/lib/console-mode";

type Listener = (payload: unknown) => void;

function fakeBridge() {
  const listeners = new Map<string, Set<Listener>>();
  let resolveSend: (value: unknown) => void = () => undefined;
  const requests: string[] = [];
  return {
    alive: true,
    turnInFlight: false,
    requests,
    on(channel: string, fn: Listener) {
      if (!listeners.has(channel)) listeners.set(channel, new Set());
      listeners.get(channel)!.add(fn);
    },
    off(channel: string, fn: Listener) {
      listeners.get(channel)?.delete(fn);
    },
    emit(channel: string, payload: unknown) {
      for (const fn of listeners.get(channel) ?? []) fn(payload);
    },
    async ensureStarted() {},
    abortInFlightSend() {
      resolveSend({ aborted: true, cancelled: true });
    },
    cancelTurn() {},
    request(method: string) {
      requests.push(method);
      if (method === "send_message") return new Promise((resolve) => (resolveSend = resolve));
      return Promise.resolve({});
    },
    resolveSend: (value: unknown) => resolveSend(value),
  };
}

const tick = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
});

describe("background replay keeps every round's tool lines in order (#33)", () => {
  it("each text segment carries the tools of its own round", async () => {
    wireChatTabsOnce();
    const a = workspace.activeChatTabId;
    const fb = fakeBridge();
    // Live order: the tab router listens before the chat adapter does.
    fb.on("event", (payload) => routeTabEvent(a, "event", payload));
    actions.setChatBackend(new CompanionChatBackend(fb as never));
    actions.sendChatMessage("multi-round work");
    await tick();
    const ev = (payload: Record<string, unknown>) => fb.emit("event", payload);
    const tool = (id: string, cmd: string, out: string) => {
      ev({ type: "tool_call", data: { name: "bash", tool_call_id: id, arguments: { command: cmd } } });
      ev({ type: "tool_result", data: { name: "bash", tool_call_id: id }, message: out });
    };

    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "round one text" });
    await tick();
    actions.tabCreate(); // A goes to the background mid-turn
    tool("r1", "echo one", "one");
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "round two text" });
    tool("r2", "echo two", "two");
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "round three text" });
    fb.resolveSend({});
    await tick();

    actions.tabActivate(a);
    await tick(60);

    const seg = (text: string) => workspace.chatMessages.find((m) => m.content === text)!;
    expect(seg("round one text").toolCalls?.map((l) => l.text)).toEqual(["✓ bash → one"]);
    expect(seg("round two text").toolCalls?.map((l) => l.text)).toEqual(["✓ bash → two"]);
    expect(seg("round three text").toolCalls ?? []).toEqual([]);
    expect(workspace.turnToolActivity).toEqual([]);
    expect(workspace.chatStreaming).toBe(false);
  });

  it("a flush that lands before the previous one was pinned adds to it", () => {
    actions.setTurnToolActivity([{ status: "ok", text: "✓ round A" }]);
    actions.setTurnToolActivity([{ status: "ok", text: "✓ round B" }]);
    expect(workspace.turnToolActivity.map((l) => l.text)).toEqual(["✓ round A", "✓ round B"]);
    actions.setTurnToolActivity([]);
    expect(workspace.turnToolActivity).toEqual([]);
  });
});

describe("a new turn starts with no tool lines left by an interrupted one (review of #33)", () => {
  it("a late flush after Esc is not pinned under the next turn", async () => {
    wireChatTabsOnce();
    const a = workspace.activeChatTabId;
    const fb = fakeBridge();
    fb.on("event", (payload) => routeTabEvent(a, "event", payload));
    actions.setChatBackend(new CompanionChatBackend(fb as never));
    const ev = (payload: Record<string, unknown>) => fb.emit("event", payload);
    const tool = (id: string, out: string) => {
      ev({ type: "tool_call", data: { name: "bash", tool_call_id: id, arguments: { command: `echo ${out}` } } });
      ev({ type: "tool_result", data: { name: "bash", tool_call_id: id }, message: out });
    };

    actions.sendChatMessage("turn one");
    await tick();
    ev({ type: "response_start" });
    tool("o1", "old");
    actions.interruptChat();
    await tick();
    // The bridge's turn_stopped flush lands after the pump finalized.
    flushTurnSummary();
    expect(workspace.turnToolActivity.map((l) => l.text)).toEqual(["✓ bash → old"]);

    actions.sendChatMessage("turn two");
    await tick();
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "t2 text" });
    tool("n1", "new");
    ev({ type: "response_start" });
    ev({ type: "response_chunk", message: "t2 more" });
    fb.resolveSend({});
    await tick(60);

    const seg = workspace.chatMessages.find((m) => m.content === "t2 text")!;
    expect(seg.toolCalls?.map((l) => l.text)).toEqual(["✓ bash → new"]);
    const tails = workspace.chatMessages.filter((m) => m.content.includes("done ("));
    expect(tails.at(-1)?.content).toContain("1 action");
  });
});

describe("a background turn settles its tab meta when it ends (#36)", () => {
  it("clears busy and finishes the Ctrl+B task without touching the active tab", () => {
    const a = workspace.activeChatTabId;
    setWorkspace("chatStreaming", true);
    actions.setTabMeta(a, { busy: true });
    expect(actions.backgroundCurrentTurn()).toBe(true);
    const taskId = workspace.backgroundTurnTaskId!;
    const b = actions.tabCreate()!;
    const bRows = workspace.chatMessages.map((m) => m.id);

    routeTabEvent(a, "turn_settled", { ok: true });

    expect(workspace.chatTabs.find((t) => t.id === a)!.busy).toBe(false);
    expect(workspace.backgroundTasks.find((t) => t.id === taskId)!.status).toBe("done");
    expect(workspace.activeChatTabId).toBe(b);
    expect(workspace.chatMessages.map((m) => m.id)).toEqual(bRows); // nothing written into B
    // Stage 4: the settle wrote the owning tab's store directly (canonical);
    // re-activation mirrors it into the workspace.
    expect(getRuntime(a).tabStore?.state.backgroundTurnTaskId).toBeNull();
    actions.tabActivate(a);
    expect(workspace.backgroundTurnTaskId).toBeNull();
  });

  it("is a no-op for the active tab (its pump settles normally)", () => {
    const a = workspace.activeChatTabId;
    actions.setTabMeta(a, { busy: true });
    routeTabEvent(a, "turn_settled", { ok: true });
    expect(workspace.chatTabs.find((t) => t.id === a)!.busy).toBe(true);
  });
});

describe("session-shaped fields are per tab (#27/#37/#38)", () => {
  it("a fresh tab starts clean and switching back restores the owner's values", () => {
    const a = workspace.activeChatTabId;
    const plan = [{ id: "t1", title: "step one", status: "pending" }] as never;
    setWorkspace("workflowArmed", true);
    setWorkspace("sessionPlan", plan);
    setWorkspace("sessionChanges", [{ path: "a.py" }] as never);
    setWorkspace("toolSelection", { strategy: "ranked", candidate_tools: 3, available_tools: 9 } as never);
    setWorkspace("scienceLoop", { phase: "observe" } as never);
    setWorkspace("knowledgeBase", { entries: 4 } as never);

    actions.tabCreate();
    expect(workspace.workflowArmed).toBe(false);
    expect(workspace.sessionPlan).toEqual([]);
    expect(workspace.sessionChanges).toEqual([]);
    expect(workspace.toolSelection).toBeNull();
    expect(workspace.scienceLoop).toBeNull();
    expect(workspace.knowledgeBase).toBeNull();

    actions.tabActivate(a);
    expect(workspace.workflowArmed).toBe(true);
    expect(workspace.sessionPlan).toHaveLength(1);
    expect(workspace.sessionChanges).toHaveLength(1);
    expect(workspace.toolSelection).not.toBeNull();
    expect(workspace.scienceLoop).not.toBeNull();
    expect(workspace.knowledgeBase).not.toBeNull();
  });
});

describe("a fresh tab takes the resource poller off the previous bridge (#38)", () => {
  it("the previous tab's bridge is not polled after Ctrl+N", async () => {
    wireChatTabsOnce();
    const a = workspace.activeChatTabId;
    const fb = fakeBridge();
    getRuntime(a).bridge = fb as never;
    retargetResourcePolling(fb as never);
    await tick();
    expect(fb.requests).toContain("get_resources");

    actions.tabCreate();
    fb.requests.length = 0;
    await tick(5_300); // one full poll interval
    expect(fb.requests).toEqual([]);
  }, 15_000);
});
