/**
 * TUI audit r2 (turns lane) — a chat turn belongs to the tab that sent it.
 *
 * #29: a send_message result that lands while its tab is in the background
 *      must not write that tab's session state into the active tab.
 * #30/#43/#58: the remote-turn watcher must survive a tab switch — pause
 *      while its tab is in the background, never write into the active tab,
 *      resume on activation and deliver the reply into its own tab.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { CompanionChatBackend, setRemotePollMsForTests } from "../src/adapters/companion-chat";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

type Listener = (payload: unknown) => void;

interface FakeBridge {
  alive: boolean;
  syncs: number;
  remoteDone: boolean;
  on(channel: string, fn: Listener): void;
  off(channel: string, fn: Listener): void;
  emit(channel: string, payload: unknown): void;
  ensureStarted(): Promise<void>;
  abortInFlightSend(): void;
  cancelTurn(): void;
  request(method: string): Promise<unknown>;
  resolveSend(value: unknown): void;
}

function fakeBridge(): FakeBridge {
  const listeners = new Map<string, Set<Listener>>();
  let resolveSend: (value: unknown) => void = () => {};
  const bridge: FakeBridge = {
    alive: true,
    syncs: 0,
    remoteDone: false,
    on(channel, fn) {
      if (!listeners.has(channel)) listeners.set(channel, new Set());
      listeners.get(channel)!.add(fn);
    },
    off(channel, fn) {
      listeners.get(channel)?.delete(fn);
    },
    emit(channel, payload) {
      for (const fn of listeners.get(channel) ?? []) fn(payload);
    },
    async ensureStarted() {},
    abortInFlightSend() {},
    cancelTurn() {},
    request(method) {
      if (method === "send_message") return new Promise((resolve) => (resolveSend = resolve));
      if (method === "session.venue_sync") {
        bridge.syncs += 1;
        return Promise.resolve(bridge.remoteDone ? remoteDoneSync() : remoteRunningSync());
      }
      if (method === "session.venue_approvals") return Promise.resolve({ approvals: [] });
      return Promise.resolve({});
    },
    resolveSend: (value) => resolveSend(value),
  };
  return bridge;
}

function remoteRunningSync() {
  return {
    runtime: { phase: "running", turn_seq: 1 },
    venue_state: "running",
    state: { total_messages: 1, session: { id: "sess-A" } },
  };
}

function remoteDoneSync() {
  return {
    adopted: true,
    runtime: { phase: "idle", turn_seq: 1 },
    venue_state: "running",
    state: {
      total_messages: 3,
      session: { id: "sess-A" },
      messages: [
        { role: "user", content: "remote q" },
        { role: "assistant", content: "the remote answer" },
      ],
    },
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  actions.resetWorkspace();
  resetTabsRegistryForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setRemotePollMsForTests(1_500);
  actions.resetWorkspace();
  resetTabsRegistryForTests();
});

/** Poll until ``done()`` holds (or the deadline passes; the asserts then report). */
async function waitFor(done: () => boolean, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!done() && Date.now() < deadline) await sleep(10);
}

describe("r2 #29 — a background tab's turn result stays in that tab", () => {
  test("result landing after a tab switch is applied only when its tab is active again", async () => {
    const bridge = fakeBridge();
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-A");
    actions.sendChatMessage("hi");
    await sleep(10);
    bridge.emit("event", { type: "response_chunk", message: "hello" });
    await sleep(10);

    const tabB = actions.tabCreate()!;
    actions.setSessionId("sess-B");
    bridge.resolveSend({ state: { session: { id: "sess-A", interaction_mode: "plan" } } });
    await sleep(20);

    // Tab B keeps its own session binding and mode.
    expect(workspace.activeChatTabId).toBe(tabB);
    expect(workspace.sessionId).toBe("sess-B");
    expect(workspace.chatTabs.find((t) => t.id === tabB)?.sessionId).toBe("sess-B");
    expect(workspace.interactionMode).not.toBe("plan");

    // Back on A: the parked result lands there. Poll instead of a fixed
    // sleep, and look for the reply rather than the last row: on the slower
    // CI runner a system notice can follow it (2026-10-18).
    actions.tabActivate(tabA);
    const reply = () => workspace.chatMessages.findLast((m) => m.role === "assistant");
    await waitFor(() => workspace.interactionMode === "plan" && reply()?.content === "hello");
    expect(workspace.sessionId).toBe("sess-A");
    expect(workspace.interactionMode).toBe("plan");
    expect(workspace.chatStreaming).toBe(false);
    expect(reply()?.content).toBe("hello");
  });
});

describe("r2 #30/#43/#58 — the remote-turn watcher survives tab switches", () => {
  test("pauses in the background, never writes into the active tab, delivers into its own tab", async () => {
    setRemotePollMsForTests(10);
    const bridge = fakeBridge();
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-A");
    setWorkspace("sessionVenue", "frontend");
    actions.sendChatMessage("remote q");
    await sleep(10);
    bridge.resolveSend({ remote: true, queued: true, state: { total_messages: 1, session: { id: "sess-A" } } });
    await sleep(60);
    const syncsBeforeSwitch = bridge.syncs;
    expect(syncsBeforeSwitch).toBeGreaterThan(0);

    actions.tabCreate();
    actions.setSessionId("sess-B");
    actions.setChatTurnStatus("tab B status");
    await sleep(40);
    const syncsAtSwitch = bridge.syncs;
    await sleep(100);
    // Paused: A's watcher keeps polling its own bridge in the background
    // only to notice the end of the turn (turn_settled, r2 #36 review); it
    // never writes into B's turn status (A's watcher used to clear it).
    expect(workspace.chatTurnStatus).toBe("tab B status");
    expect(workspace.venueRuntime).toBeNull();

    // The remote worker finishes while A is in the background.
    bridge.remoteDone = true;
    actions.tabActivate(tabA);
    await sleep(100);
    expect(bridge.syncs).toBeGreaterThan(syncsAtSwitch);
    expect(workspace.chatStreaming).toBe(false);
    const texts = workspace.chatMessages.map((m) => m.content);
    expect(texts.some((t) => t.includes("the remote answer"))).toBe(true);
    expect(texts.some((t) => t.includes("empty chat stream"))).toBe(false);
  });
});
