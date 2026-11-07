/**
 * Claude-Code parity store actions: rewind menu, verbose transcript, queue
 * take-back / send-now, and the new keymap commands registered.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { ChatMessage } from "../src/types";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

const msg = (id: string, role: ChatMessage["role"], content: string, queued = false): ChatMessage => ({
  id,
  role,
  content,
  timestamp: new Date().toISOString(),
  ...(queued ? { queued: true } : {}),
});

describe("rewind", () => {
  it("refuses with a notice when there is no earlier prompt", () => {
    expect(actions.openRewindMenu()).toBe(false);
    expect(workspace.rewindMenuOpen).toBe(false);
  });

  it("opens, and rewinding seeds the prompt and marks the truncation point", () => {
    setWorkspace("chatMessages", [msg("u1", "user", "first"), msg("a1", "assistant", "reply")]);
    expect(actions.openRewindMenu()).toBe(true);
    actions.rewindTo(0);
    expect(workspace.rewindMenuOpen).toBe(false);
    expect(workspace.editResendFromIndex).toBe(0);
    expect(workspace.pendingChatDraft).toBe("first");
  });

  it("is closed by the overlay sweep", () => {
    setWorkspace("chatMessages", [msg("u1", "user", "first")]);
    actions.openRewindMenu();
    actions.closeAllOverlays();
    expect(workspace.rewindMenuOpen).toBe(false);
  });
});

describe("verbose transcript", () => {
  it("toggles and carries the thinking block with it", () => {
    actions.toggleVerboseTranscript();
    expect(workspace.verboseTranscript).toBe(true);
    expect(workspace.thinking.expanded).toBe(true);
    actions.toggleVerboseTranscript();
    expect(workspace.verboseTranscript).toBe(false);
    expect(workspace.thinking.expanded).toBe(false);
  });
});

describe("queued prompts", () => {
  const seedQueue = () => {
    setWorkspace("chatMessages", [
      msg("u1", "user", "running"),
      msg("q1", "user", "next one", true),
      msg("q2", "user", "and this", true),
    ]);
    setWorkspace("pendingChatQueue", ["q1", "q2"]);
  };

  it("take-back removes the queued bubbles and seeds them as one draft", () => {
    seedQueue();
    expect(actions.takeBackQueued()).toBe(true);
    expect(workspace.pendingChatQueue).toEqual([]);
    expect(workspace.chatMessages.some((m) => m.queued)).toBe(false);
    expect(workspace.pendingChatDraft).toBe("next one\n\nand this");
  });

  it("take-back with nothing queued does nothing", () => {
    expect(actions.takeBackQueued()).toBe(false);
  });

  it("send-now drains the queue into one new turn", () => {
    seedQueue();
    expect(actions.sendQueuedNow()).toBe(true);
    expect(workspace.pendingChatQueue).toEqual([]);
    const users = workspace.chatMessages.filter((m) => m.role === "user");
    expect(users.at(-1)?.content).toBe("next one\n\nand this");
    expect(users.some((m) => m.queued)).toBe(false);
  });
});

// ── W2: /btw side questions and background work ─────────────────────────────


describe("/btw", () => {
  it("answers through session.side_question without touching the transcript", async () => {
    const calls: Array<[string, unknown]> = [];
    setCompanionBridgeForTests({
      request: async (method: string, params: unknown) => {
        calls.push([method, params]);
        return { answer: "because Y" };
      },
    } as never);
    try {
      const before = workspace.chatMessages.length;
      await actions.askBtw("why X?");
      expect(calls).toEqual([["session.side_question", { question: "why X?" }]]);
      expect(workspace.btw.open).toBe(true);
      expect(workspace.btw.exchanges.at(-1)).toMatchObject({ question: "why X?", answer: "because Y" });
      expect(workspace.chatMessages.length).toBe(before); // never added to the history
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  it("keeps the 5 newest exchanges and records errors", async () => {
    setCompanionBridgeForTests({
      request: async () => {
        throw new Error("bridge down");
      },
    } as never);
    try {
      for (let i = 0; i < 7; i += 1) await actions.askBtw(`q${i}`);
      expect(workspace.btw.exchanges.map((x) => x.question)).toEqual(["q2", "q3", "q4", "q5", "q6"]);
      expect(workspace.btw.exchanges.at(-1)?.error).toBe("bridge down");
      actions.stepBtw(-10);
      expect(workspace.btw.index).toBe(0);
      actions.clearBtw();
      expect(workspace.btw.exchanges).toEqual([]);
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});

describe("background work (Ctrl+B)", () => {
  it("backgrounding needs a running turn", () => {
    expect(actions.backgroundCurrentTurn()).toBe(false);
    expect(workspace.backgroundTasks).toEqual([]);
  });

  it("a backgrounded turn is registered and an interrupt cancels it", () => {
    setWorkspace("chatMessages", [msg("u1", "user", "long analysis please")]);
    setWorkspace("chatStreaming", true);
    expect(actions.backgroundCurrentTurn()).toBe(true);
    const task = workspace.backgroundTasks.at(-1);
    expect(task).toMatchObject({ kind: "turn", status: "running", label: "long analysis please" });
    actions.interruptChat();
    expect(workspace.backgroundTasks.at(-1)?.status).toBe("cancelled");
    expect(workspace.backgroundTurnTaskId).toBeNull();
  });

  it("tasks finish with a status and detail", () => {
    const id = actions.addBackgroundTask("shell", "$ make");
    actions.finishBackgroundTask(id, "failed", "exit 2");
    expect(workspace.backgroundTasks.at(-1)).toMatchObject({ status: "failed", detail: "exit 2" });
  });
});

describe("pane focus (2026-10-06 focus model)", () => {
  it("opening a pane leaves chat focused; Tab/click moves the keys in", () => {
    // Formerly focused-on-open (W3): a docked pane disabled the composer
    // entirely (user report 2026-10-06). Verbs stay reachable via the
    // explicit focus move (whenPaneFocused gates them, not paneView).
    actions.openPane("sessions");
    expect(workspace.paneFocus).toBe(false);
    actions.togglePaneFocus();
    expect(workspace.paneFocus).toBe(true);
    actions.togglePaneFocus();
    expect(workspace.paneFocus).toBe(false);
    actions.closePane();
    expect(workspace.paneFocus).toBe(false);
  });
});
