/**
 * ADR-099 D-2 (RS-1 round-2 fix W2-5): a session without a web-access
 * choice asks before ANY turn, not only before a typed send. Edit-resend,
 * retries, queue flushes, the title screen, slash relays and shell/term
 * shares all go through `actions.sendChatMessage`; on the old code they
 * started the turn with no question.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { actions, activeTab, resetWorkspace, setScopedField, workspace } from "../src/stores";
import { applyWebChoice, type WebChoiceDeps } from "../src/components/web-access-overlay";

type Prompt = { purpose?: string; pendingText?: string; pendingRelay?: boolean } | null | undefined;

function prompt(): Prompt {
  return (workspace as unknown as Record<string, Prompt>).webAccessPrompt;
}

function tabWeb(mode: string): void {
  setScopedField("webAccess" as never, { mode, domains: [], chosenAt: "", source: mode ? "prompt" : "" } as never);
}

function userRows(): string[] {
  return activeTab().chatMessages.filter((m) => m.role === "user").map((m) => m.content);
}

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream() {
      yield { delta: "ok", done: true };
    },
  });
});

describe("every send path asks on a session without a choice", () => {
  it("a store send (retry, queue flush, edit-resend) holds the message", () => {
    tabWeb("");
    actions.sendChatMessage("rerun the solver");
    expect(prompt()?.purpose).toBe("first-submit");
    expect(prompt()?.pendingText).toBe("rerun the solver");
    expect(prompt()?.pendingRelay).toBe(false);
    expect(userRows()).toEqual([]);
    expect(workspace.chatStreaming).toBe(false);
  });

  it("the title screen's submit asks too", async () => {
    tabWeb("");
    await actions.submitChatText("hello from the title screen");
    expect(prompt()?.pendingText).toBe("hello from the title screen");
    expect(userRows()).toEqual([]);
  });

  it("a relay (shell output, slash relay) is held as a relay and dropped on cancel", () => {
    tabWeb("");
    actions.sendChatMessage("$ ls\nresult.csv", { relay: true });
    expect(prompt()?.pendingRelay).toBe(true);
    expect(userRows()).toEqual([]);
    actions.closeWebAccessPrompt();
    // machine content never lands in the composer
    expect(workspace.pendingChatDraft ?? "").not.toContain("result.csv");
  });

  it("the answer sends a held relay as a relay", async () => {
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
      await applyWebChoice(
        { purpose: "first-submit", remote: false, pendingText: "$ ls", pendingRelay: true },
        { mode: "none", domains: [] },
        deps,
      );
      expect(sent).toEqual([{ text: "$ ls", relay: true }]);
    } finally {
      (actions as { sendChatMessage: typeof original }).sendChatMessage = original;
    }
  });

  it("a session with a choice (or a pre-RS-1 bridge) sends at once", () => {
    tabWeb("none");
    actions.sendChatMessage("go");
    expect(prompt() ?? null).toBeNull();
    expect(userRows()).toEqual(["go"]);
  });
});
