/**
 * TUI audit r2 (2026-10-17), tabs lane — closing and reopening chat tabs.
 *
 * #31 closing a busy background tab settled its turn into the ACTIVE tab
 *     (canned failure row, cleared streaming flags, popped + re-sent queue);
 * #32 closing a background tab wiped the active turn's tool accumulators;
 * #35 a tab closed mid-turn reopened stuck "streaming" with nothing to finish it;
 * #39 `/tab close <bad n>` closed the active tab instead of reporting an error.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { flushTurnSummary, onBridgeEvent } from "../src/bridge/companion-runtime-bridge";
import { handleWaveIntercepts } from "../src/commands/wave-intercepts";
import { setDemoMode } from "../src/lib/console-mode";

type Listener = (payload: unknown) => void;

/** A tab bridge whose send_message stays pending until resolved or aborted. */
function fakeBridge() {
  const listeners = new Map<string, Set<Listener>>();
  let resolveSend: (value: unknown) => void = () => undefined;
  return {
    alive: true,
    turnInFlight: false,
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
      if (method === "send_message") return new Promise((resolve) => (resolveSend = resolve));
      return Promise.resolve({});
    },
  };
}

const tick = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
});

describe("closing a busy background tab (#31)", () => {
  it("never settles the closed tab's turn into the active tab", async () => {
    actions.setChatBackend(new CompanionChatBackend(fakeBridge() as never));
    const a = workspace.activeChatTabId;
    actions.sendChatMessage("long tool turn in A");
    await tick();
    const b = actions.tabCreate()!;
    // B runs its own turn with a queued follow-up.
    setWorkspace("chatStreaming", true);
    setWorkspace("isTyping", true);
    setWorkspace("chatMessages", (list) => [
      ...list,
      { id: "q1", role: "user", content: "queued in B", timestamp: "", queued: true },
    ]);
    setWorkspace("pendingChatQueue", ["q1"]);
    const rowsBefore = workspace.chatMessages.map((m) => m.id);

    expect(await actions.tabClose(a)).toBe(true);
    await tick(50);

    expect(workspace.activeChatTabId).toBe(b);
    expect(workspace.chatMessages.map((m) => m.id)).toEqual(rowsBefore);
    expect(workspace.pendingChatQueue).toEqual(["q1"]);
    expect(workspace.chatStreaming).toBe(true);
    expect(workspace.isTyping).toBe(true);
    expect(workspace.chatFallbackActive).toBe(false);
  });
});

describe("closing a background tab keeps the active turn's accumulators (#32)", () => {
  it("the active tab's in-flight tool line survives the close", async () => {
    const a = workspace.activeChatTabId;
    actions.tabCreate();
    onBridgeEvent({ type: "tool_call", data: { name: "bash", tool_call_id: "c1", arguments: { command: "ls" } } });
    onBridgeEvent({ type: "tool_result", data: { name: "bash", tool_call_id: "c1" }, message: "ok" });
    await actions.tabClose(a);
    flushTurnSummary();
    // The close used to reset the live accumulators: 0 lines.
    expect(workspace.turnToolActivity.map((l) => l.text)).toEqual(["✓ bash → ok"]);
  });
});

describe("reopening a tab closed mid-turn (#35)", () => {
  it("comes back idle: no streaming flags, card, queue or streaming row", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { delta: "fresh reply" };
        yield { done: true };
      },
    });
    actions.tabCreate();
    setWorkspace("chatStreaming", true);
    setWorkspace("isTyping", true);
    setWorkspace("chatTurnStatus", "running bash");
    setWorkspace("toolApproval", { id: "ap-1", tool: "bash", description: "run" } as never);
    setWorkspace("chatMessages", (list) => [
      ...list,
      { id: "s1", role: "assistant", content: "half", timestamp: "", streaming: true },
      { id: "q1", role: "user", content: "later", timestamp: "", queued: true },
    ]);
    setWorkspace("pendingChatQueue", ["q1"]);

    await actions.tabClose();
    expect(await actions.tabReopenClosed()).toBe(true);

    expect(workspace.chatStreaming).toBe(false);
    expect(workspace.isTyping).toBe(false);
    expect(workspace.chatTurnStatus).toBeNull();
    expect(workspace.toolApproval).toBeNull();
    expect(workspace.pendingChatQueue).toEqual([]);
    const half = workspace.chatMessages.find((m) => m.id === "s1")!;
    expect(half.content).toBe("half"); // transcript kept
    expect(half.streaming).toBe(false);
    expect(workspace.chatMessages.find((m) => m.id === "q1")!.queued).toBe(false);

    // A new prompt runs as a turn instead of queueing forever.
    actions.sendChatMessage("hello");
    expect(workspace.pendingChatQueue).toEqual([]);
    await tick(50);
    expect(workspace.chatMessages.some((m) => m.content === "fresh reply")).toBe(true);
  });
});

describe("/tab close <n> (#39)", () => {
  async function run(args: string) {
    return handleWaveIntercepts({ name: "tab", args });
  }

  it("an out-of-range or non-numeric index is an error and closes nothing", async () => {
    actions.tabCreate();
    actions.tabCreate();
    const active = workspace.activeChatTabId;
    for (const bad of ["close 9", "close 0", "close -1", "close abc", "close 1.5"]) {
      const result = await run(bad);
      expect(result?.kind).toBe("error");
      expect(workspace.chatTabs).toHaveLength(3);
      expect(workspace.activeChatTabId).toBe(active);
    }
  });

  it("an inherited object key is not a verb (review: /tab toString)", async () => {
    for (const verb of ["toString", "constructor", "hasOwnProperty", "__proto__"]) {
      expect((await run(verb))?.kind).toBe("error");
    }
  });

  it("a valid index closes that tab; no index closes the active one", async () => {
    const first = workspace.activeChatTabId;
    actions.tabCreate();
    const third = actions.tabCreate()!;
    expect((await run("close 1"))?.kind).toBe("handled");
    expect(workspace.chatTabs.map((t) => t.id)).not.toContain(first);
    expect((await run("close"))?.kind).toBe("handled");
    expect(workspace.chatTabs.map((t) => t.id)).not.toContain(third);
    expect(workspace.chatTabs).toHaveLength(1);
  });
});
