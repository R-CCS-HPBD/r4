/**
 * TUI audit r2 (2026-10-17), glue lane — fixes from the review of the
 * round-2 glue changes.
 *
 * #17  the user row is tagged when the turn OPENS (bridge `turn_opened`),
 *      so a cancelled turn can still be edited and resent; a remote session
 *      and a slash-command row are refused with their real reason; a second
 *      Enter during a rewind is ignored, and a rewind finishes in the tab
 *      that asked for it.
 * #20  each compaction gets its own row at the bottom; the /compact reply
 *      and its event still share one.
 * #36  a remote turn whose tab is in the background still settles
 *      (`turn_settled`) when it ends.
 * #67  Ctrl+D with a draft and a focused pane does not arm the exit.
 * followup  server-built follow-up prompts are relays: they never fire an
 *      armed teamup.
 */
import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import type { ChatMessage } from "../src/types";
import { CompanionChatBackend, setRemotePollMsForTests } from "../src/adapters/companion-chat";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { onBridgeEvent, resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { wireChatTabsOnce } from "../src/tabs/wiring";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setDemoMode } from "../src/lib/console-mode";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * A channel-aware fake bridge (an EventEmitter, like CompanionBridgeClient).
 * `hold` names methods whose answer waits for `release(method, value)`.
 */
class FakeBridge extends EventEmitter {
  alive = true;
  turnInFlight = false;
  readonly calls: Array<[string, Record<string, unknown>]> = [];
  answer: (method: string, params: Record<string, unknown>) => unknown = () => ({});
  readonly hold = new Set<string>();
  private held = new Map<string, Array<(value: unknown) => void>>();
  onCancel: (() => void) | null = null;

  async ensureStarted(): Promise<void> {}
  abortInFlightSend(): void {}
  cancelTurn(): void {
    this.onCancel?.();
  }
  request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    this.calls.push([method, params]);
    if (this.hold.has(method)) {
      return new Promise((resolve) => this.held.set(method, [...(this.held.get(method) ?? []), resolve]));
    }
    return Promise.resolve(this.answer(method, params));
  }
  release(method: string, value: unknown): void {
    const waiting = this.held.get(method) ?? [];
    this.held.delete(method);
    for (const resolve of waiting) resolve(value);
  }
  count(method: string): number {
    return this.calls.filter(([m]) => m === method).length;
  }
}

const row = (id: string, role: ChatMessage["role"], content: string, turnId?: string): ChatMessage => ({
  id,
  role,
  content,
  timestamp: new Date().toISOString(),
  ...(turnId ? { turnId } : {}),
});

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  resetCompanionRuntimeBridgeForTests();
  resetSlashCatalogCache();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  setCompanionBridgeForTests(null);
  setRemotePollMsForTests(1_500);
  resetWorkspace();
  resetTabsRegistryForTests();
});

function useCompanion(bridge: FakeBridge): void {
  setCompanionBridgeForTests(bridge as never);
  actions.setChatBackend(new CompanionChatBackend(bridge as never));
}

describe("r2 #17 review — a cancelled turn can still be rewound", () => {
  it("turn_opened tags the row; Esc, edit and resend cut by that id", async () => {
    const bridge = new FakeBridge();
    bridge.hold.add("send_message");
    bridge.answer = (method) => (method === "session.truncate" ? { ok: true, removed: 1, messages: 0 } : {});
    useCompanion(bridge);
    actions.sendChatMessage("hello");
    await sleep(10);
    // The bridge committed the user message as t3, then the user hits Esc.
    bridge.emit("event", { type: "turn_opened", message: "", data: { turn_id: "t3" } });
    await sleep(10);
    bridge.onCancel = () => bridge.release("send_message", { cancelled: true, response: "", turn_id: "t3" });
    actions.interruptChat();
    await sleep(20);
    const idx = workspace.chatMessages.findIndex((m) => m.role === "user" && m.content === "hello");
    expect(workspace.chatMessages[idx]?.turnId).toBe("t3");
    expect(actions.beginEditResend(idx)).toBe(true);
    bridge.hold.delete("send_message");
    await actions.submitChatText("hello, edited");
    expect(bridge.calls.find(([m]) => m === "session.truncate")?.[1]).toEqual({ turn_id: "t3" });
  });

  it("a remote session names the venue as the reason", async () => {
    const bridge = new FakeBridge();
    useCompanion(bridge);
    setWorkspace("sessionVenue", "frontend");
    setWorkspace("chatMessages", [row("u1", "user", "q"), row("a1", "assistant", "a")]);
    actions.beginEditResend(0);
    await actions.submitChatText("q2");
    const notice = workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content).join("\n");
    expect(notice).toContain("cannot rewind a remote session");
    expect(notice).not.toContain("no session id");
    expect(bridge.count("session.truncate") + bridge.count("send_message")).toBe(0);
    // 2026-10-19: the venue refusal is permanent too — the edit ends here,
    // so the next Enter posts a normal turn instead of refusing again.
    expect(workspace.editResendFromIndex).toBeNull();
    await actions.submitChatText("plain follow-up");
    await sleep(30);
    expect(bridge.count("send_message")).toBe(1);
    expect(bridge.count("session.truncate")).toBe(0);
  });

  it("a slash-command row is not a chat turn", async () => {
    const bridge = new FakeBridge();
    useCompanion(bridge);
    setWorkspace("chatMessages", [row("u1", "user", "/compact"), row("s1", "system", "compacted")]);
    actions.beginEditResend(0);
    await actions.submitChatText("plain words");
    const notice = workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content).join("\n");
    expect(notice).toContain("cannot rewind to a slash command");
    // 2026-10-19: a permanent refusal cancels the edit — the marker used to
    // stay, so every later Enter re-hit the refusal and nothing ever sent.
    expect(workspace.editResendFromIndex).toBeNull();
    await actions.submitChatText("later turn");
    await sleep(30);
    expect(bridge.count("send_message")).toBe(1);
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "later turn")).toBe(true);
    const later = workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content).join("\n");
    expect(later.match(/cannot rewind/g)?.length ?? 0).toBe(1);
  });

  it("a second Enter during the rewind does not start another one", async () => {
    const bridge = new FakeBridge();
    bridge.hold.add("session.truncate");
    useCompanion(bridge);
    setWorkspace("chatMessages", [row("u1", "user", "q", "t1"), row("a1", "assistant", "a")]);
    actions.beginEditResend(0);
    const first = actions.submitChatText("q2");
    const second = actions.submitChatText("q2");
    await sleep(10);
    bridge.release("session.truncate", { ok: true });
    await Promise.all([first, second]);
    await sleep(20);
    expect(bridge.count("session.truncate")).toBe(1);
    expect(bridge.count("send_message")).toBe(1);
    expect(workspace.chatMessages.some((m) => m.errorCard)).toBe(false);
  });

  it("a tab switch during the rewind sends in the tab that asked, once it is back", async () => {
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    bridge.hold.add("session.truncate");
    useCompanion(bridge);
    setWorkspace("chatMessages", [row("u1", "user", "q", "t1"), row("a1", "assistant", "a")]);
    actions.beginEditResend(0);
    const pending = actions.submitChatText("q2");
    await sleep(5);
    actions.tabCreate();
    const tabB = workspace.activeChatTabId;
    expect(tabB).not.toBe(tabA);
    bridge.release("session.truncate", { ok: true });
    await sleep(20);
    expect(bridge.count("send_message")).toBe(0);
    expect(workspace.chatMessages.some((m) => m.content === "q2")).toBe(false);
    actions.tabActivate(tabA);
    await pending;
    await sleep(20);
    expect(bridge.count("send_message")).toBe(1);
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "q2")).toBe(true);
    expect(workspace.chatMessages.some((m) => m.content === "a")).toBe(false);
  });
});

describe("r2 #20 review — one row per compaction", () => {
  it("a second compaction lands at the bottom; the first row stays", () => {
    onBridgeEvent({ type: "context_compact", message: "Conversation compacted.", data: { kept_messages: 4 } });
    setWorkspace("chatMessages", (list) => [...list, row("u2", "user", "next"), row("a2", "assistant", "reply")]);
    onBridgeEvent({ type: "context_compact", message: "Conversation compacted.", data: { kept_messages: 6 } });
    const lines = workspace.chatMessages.filter((m) => /context compacted/.test(m.content));
    expect(lines).toHaveLength(2);
    expect(lines[0]?.content).toContain("kept 4");
    const systems = workspace.chatMessages.filter((m) => m.role === "system");
    expect(systems[systems.length - 1]?.content).toContain("kept 6");
  });

  it("/compact and its event still share one row", async () => {
    const bridge = new FakeBridge();
    bridge.answer = (method) => {
      if (method !== "session.compact") return {};
      onBridgeEvent({ type: "context_compact", message: "Conversation compacted.", data: { kept_messages: 4 } });
      return { changed: true, from: 20, to: 6, keep_recent: 4 };
    };
    useCompanion(bridge);
    onBridgeEvent({ type: "context_compact", message: "Conversation compacted.", data: { kept_messages: 2 } });
    setWorkspace("chatMessages", (list) => [...list, row("u2", "user", "/compact")]);
    expect((await dispatchSlashCommand("/compact")).kind).toBe("handled");
    const lines = workspace.chatMessages.filter((m) => /compacted/.test(m.content));
    expect(lines).toHaveLength(2);
    expect(lines[1]?.content).toContain("20 → 6");
  });
});

describe("r2 #36 review — a background remote turn settles", () => {
  it("turn_settled fires while the owning tab is in the background", async () => {
    setRemotePollMsForTests(5);
    wireChatTabsOnce();
    const tabA = workspace.activeChatTabId;
    const bridge = new FakeBridge();
    bridge.hold.add("send_message");
    let ended = false;
    bridge.answer = (method) =>
      method === "session.venue_sync"
        ? ended
          ? { adopted: true, runtime: { phase: "exited" }, state: { total_messages: 4, messages: [] } }
          : { adopted: true, runtime: { phase: "running" }, state: { total_messages: 2 } }
        : {};
    const settled: unknown[] = [];
    bridge.on("turn_settled", (p: unknown) => settled.push(p));
    actions.setChatBackend(new CompanionChatBackend(bridge as never));
    actions.sendChatMessage("remote work");
    await sleep(5);
    // Queued to the remote worker; the user moves to another tab at once.
    actions.tabCreate();
    bridge.release("send_message", { remote: true, queued: true, state: { total_messages: 2 } });
    await sleep(30);
    expect(settled).toEqual([]);
    ended = true;
    await sleep(40);
    expect(settled).toHaveLength(1);
    expect(workspace.activeChatTabId).not.toBe(tabA);
    actions.tabActivate(tabA);
    await sleep(40);
  });
});

describe("followup review — server prompts are relays", () => {
  it("an armed teamup is not fired by a /code review prompt", async () => {
    const bridge = new FakeBridge();
    bridge.answer = (method) =>
      method === "dispatch_slash_command"
        ? { handled: true, output: [], state: {}, follow_up_messages: ["Review a.py."], dropped_turn_ids: [] }
        : method === "send_message"
          ? { response: "ok", state: {} }
          : {};
    useCompanion(bridge);
    actions.setTeamupArmed(true, "code");
    await dispatchSlashCommand("/code review a.py");
    await sleep(20);
    expect(workspace.teamupArmed).toBe(true);
    expect(bridge.calls.find(([m]) => m === "send_message")?.[1]?.["text"]).toBe("Review a.py.");
  });
});

describe("r2 #67 review — Ctrl+D keeps a draft", () => {
  it("with a draft and a focused pane, Ctrl+D does not arm the exit", async () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    stopBackendPoller();
    stopKnowledgePoller();
    agentActions.resetAgentRuntime();
    const setup = await testRender(() => <App />, { width: 160, height: 50 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await sleep(60);
    await setup.renderOnce();
    await setup.mockInput.typeText("half typed");
    await setup.renderOnce();
    actions.openPane("infrastructure");
    await sleep(120);
    await setup.renderOnce();
    setup.mockInput.pressTab();
    await sleep(60);
    await setup.renderOnce();
    expect(workspace.paneFocus).toBe(true);
    setup.mockInput.pressKey("d", { ctrl: true });
    await sleep(60);
    await setup.renderOnce();
    expect(workspace.chatMessages.some((m) => m.content.includes("Press Ctrl+D again"))).toBe(false);
    expect(workspace.chatDraft).toBe("half typed");
    setup.renderer.destroy();
    stopBackendPoller();
    stopKnowledgePoller();
    await sleep(50);
  });
});
