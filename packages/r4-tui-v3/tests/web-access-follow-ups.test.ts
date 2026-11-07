/**
 * ADR-099 D-2, RS-1 round-3 fixes.
 *
 * N1 a slash command's follow-up turns (/retry, /code edit, /agent chat,
 *    /search) on a session without a web-access choice: /retry has already
 *    cut its turn from the session and the transcript, so cancelling the
 *    question must put the prompt back in the composer (it was dropped as
 *    a relay), and a list of several follow-ups is held whole (only the
 *    last one survived). A shell share is still dropped on cancel.
 * N2 a local `default_off` (an `r4 -p` run nobody chose for) is not a
 *    choice: the console asks before a turn (`/venue start`: in
 *    web-access-flows.test.tsx).
 *
 * Imports only modules that existed before the fix, so on the old code the
 * assertions fail, not the imports.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, resetWorkspace, setScopedField, setWorkspace, workspace } from "../src/stores";
import type { ChatMessage } from "../src/types";
import { CompanionChatBackend } from "../src/adapters/companion-chat";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { resetCompanionRuntimeBridgeForTests } from "../src/bridge/companion-runtime-bridge";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { getRuntime, resetTabsRegistryForTests } from "../src/tabs/registry";
import { setDemoMode } from "../src/lib/console-mode";
import { applyWebChoice, type WebChoiceDeps } from "../src/components/web-access-overlay";
import { needsWebChoice, type WebAccessChoice } from "../src/lib/web-access";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type Prompt = { purpose?: string; pendingText?: string; pendingRelay?: boolean; pendingMore?: string[] };

function prompt(): Prompt | null | undefined {
  return (workspace as unknown as Record<string, Prompt | null>).webAccessPrompt;
}

function tabWeb(mode: string, source = mode ? "prompt" : ""): void {
  setScopedField("webAccess" as never, { mode, domains: [], chosenAt: "", source } as never);
}

const row = (id: string, role: ChatMessage["role"], content: string, turnId?: string): ChatMessage => ({
  id,
  role,
  content,
  timestamp: new Date().toISOString(),
  ...(turnId ? { turnId } : {}),
});

/** A bridge answering /retry: the turn t4 is cut, its prompt handed back. */
function retryBridge(followUps: string[]) {
  const calls: string[] = [];
  return {
    calls,
    alive: true,
    turnInFlight: false,
    ensureStarted: async () => undefined,
    abortInFlightSend: () => undefined,
    cancelTurn: () => undefined,
    on: () => undefined,
    off: () => undefined,
    request: async (method: string) => {
      calls.push(method);
      if (method === "list_slash_commands") {
        return { commands: [{ name: "retry", help: "Retry", takesArgs: false }] };
      }
      if (method === "dispatch_slash_command") {
        return {
          handled: true,
          output: [{ kind: "system", text: "retrying: what is x" }],
          state: {},
          follow_up_messages: followUps,
          dropped_turn_ids: ["t4"],
        };
      }
      return {};
    },
  };
}

async function retryOnUnchosenSession(followUps: string[]): Promise<ReturnType<typeof retryBridge>> {
  const bridge = retryBridge(followUps);
  setCompanionBridgeForTests(bridge as never);
  actions.setChatBackend(new CompanionChatBackend(bridge as never));
  setWorkspace("chatMessages", [
    row("u1", "user", "what is x", "t4"),
    row("a1", "assistant", "old answer"),
  ]);
  tabWeb("");
  await dispatchSlashCommand("/retry");
  await sleep(10);
  return bridge;
}

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  resetCompanionRuntimeBridgeForTests();
  resetSlashCatalogCache();
  actions.closeAllOverlays();
});
afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
  resetTabsRegistryForTests();
});

describe("N1 — slash follow-ups held by the web-access question", () => {
  test("a cancelled /retry puts the cut prompt back in the composer", async () => {
    const bridge = await retryOnUnchosenSession(["[Project context] Active project: P1.\n\nwhat is x"]);
    expect(prompt()?.purpose).toBe("first-submit");
    expect(bridge.calls).not.toContain("send_message");
    actions.closeWebAccessPrompt();
    // the turn is gone from the session and the transcript: only the composer has it
    expect(workspace.chatMessages.map((m) => m.content)).not.toContain("what is x");
    expect(workspace.pendingChatDraft).toBe("what is x");
  });

  test("several follow-ups are held whole, and come back together", async () => {
    await retryOnUnchosenSession(["first follow-up", "second follow-up"]);
    actions.closeWebAccessPrompt();
    expect(workspace.pendingChatDraft).toBe("first follow-up\n\nsecond follow-up");
  });

  test("answering sends every held follow-up, in order, as relays", async () => {
    await retryOnUnchosenSession(["first follow-up", "second follow-up"]);
    const held = prompt();
    expect(held).toBeTruthy();
    const sent: Array<{ text: string; relay?: boolean }> = [];
    const original = actions.sendChatMessage;
    (actions as { sendChatMessage: typeof original }).sendChatMessage = (text, opts) =>
      void sent.push({ text, relay: opts?.relay });
    try {
      const deps: WebChoiceDeps = {
        newSession: async () => true,
        setSessionWebAccess: async () => true,
        fetchSessions: () => undefined,
      };
      await applyWebChoice(held as never, { mode: "none", domains: [] }, deps);
    } finally {
      (actions as { sendChatMessage: typeof original }).sendChatMessage = original;
    }
    expect(sent).toEqual([
      { text: "first follow-up", relay: true },
      { text: "second follow-up", relay: true },
    ]);
  });

  test("a failed answer keeps the held follow-ups in the composer", async () => {
    await retryOnUnchosenSession(["what is x"]);
    const deps: WebChoiceDeps = {
      newSession: async () => true,
      setSessionWebAccess: async () => false,
      fetchSessions: () => undefined,
    };
    await applyWebChoice(prompt() as never, { mode: "none", domains: [] }, deps);
    expect(workspace.pendingChatDraft).toBe("what is x");
  });

  test("a shell share is still dropped on cancel", () => {
    tabWeb("");
    actions.sendChatMessage("$ ls\nresult.csv", { relay: true });
    actions.closeWebAccessPrompt();
    expect(workspace.pendingChatDraft ?? "").not.toContain("result.csv");
  });

  test("a second held send does not replace the first", () => {
    tabWeb("");
    actions.sendChatMessage("first typed");
    actions.sendChatMessage("second typed");
    actions.closeWebAccessPrompt();
    expect(workspace.pendingChatDraft).toBe("first typed\n\nsecond typed");
  });
});

describe("N2 — a local default_off is not a choice", () => {
  const defaultOff: WebAccessChoice = { mode: "none", domains: [], chosenAt: "", source: "default_off" };

  test("the console asks before a turn on a default_off session", () => {
    expect(needsWebChoice(defaultOff, undefined)).toBe(true);
    expect(needsWebChoice({ ...defaultOff, source: "prompt" }, undefined)).toBe(false);
    expect(needsWebChoice(defaultOff, "running")).toBe(false);
    tabWeb("none", "default_off");
    actions.sendChatMessage("hello");
    expect(prompt()?.purpose).toBe("first-submit");
  });
});

// RS-1 round 4, web R4-1: the failed-answer path skipped returnHeld's draft
// check, so a /retry prompt overwrote a draft typed during the 15 s call.
describe("R4-1 — a failed answer never overwrites the composer draft", () => {
  const failing: WebChoiceDeps = {
    newSession: async () => true,
    setSessionWebAccess: async () => false,
    fetchSessions: () => undefined,
  };

  test("a draft typed during the call stays; a notice carries the held text", async () => {
    tabWeb("");
    actions.sendFollowUps(["what is x"]);
    const held = prompt();
    actions.closeWebAccessPrompt({ sent: true }); // what confirm() does before the await
    actions.setChatDraft("my unsent draft");
    const seq = workspace.pendingChatDraftSeq;
    const before = workspace.chatMessages.length;
    await applyWebChoice(held as never, { mode: "none", domains: [] }, failing);
    expect(workspace.pendingChatDraftSeq).toBe(seq);
    const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
    expect(added.some((t) => t.includes("what is x"))).toBe(true);
  });

  test("a failed answer after a tab switch does not fill the other tab's composer", async () => {
    tabWeb("");
    const tabA = workspace.activeChatTabId;
    actions.sendFollowUps(["what is x"]);
    const held = prompt();
    actions.closeWebAccessPrompt({ sent: true });
    const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
    actions.tabActivate(tabA);
    const seq = workspace.pendingChatDraftSeq;
    const switchDuringCall: WebChoiceDeps["setSessionWebAccess"] = async () => {
      actions.tabActivate(tabB); // B's composer is empty
      return false;
    };
    await applyWebChoice(held as never, { mode: "none", domains: [] }, { ...failing, setSessionWebAccess: switchDuringCall });
    expect(workspace.activeChatTabId).toBe(tabB);
    expect(workspace.pendingChatDraftSeq).toBe(seq);
    const onA = getRuntime(tabA).tabStore!.state.chatMessages.map((m) => String(m.content ?? ""));
    expect(onA.some((t) => t.includes("what is x"))).toBe(true);
  });
});
