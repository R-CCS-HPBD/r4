/**
 * Chat store tests.
 */

import { describe, it, expect, beforeEach, spyOn } from "bun:test";
import { actions, workspace, resetWorkspace, setWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { getCompanionBridge } from "../src/bridge/jsonl-client";
import type { ChatMessage } from "../src/types";

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function waitForChatCondition(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 1000;
  while (!condition() && Date.now() < deadline) await Bun.sleep(5);
  expect(condition()).toBe(true);
}

it("remote followups are appended once and never popped or resubmitted by TUI", async () => {
  const calls: string[] = [];
  const request = spyOn(getCompanionBridge(), "request").mockImplementation(async (method, params) => {
    calls.push(`${method}:${params?.action}`);
    return { applied: true, queue: [] };
  });
  let finishTurn = () => {};
  const ready = new Promise<void>((resolve) => { finishTurn = resolve; });
  let sends = 0;
  const transcript: ChatMessage[] = [
    { id: "u1", role: "user", content: "first", timestamp: "" },
    { id: "a1", role: "assistant", content: "reply one", timestamp: "" },
    { id: "u2", role: "user", content: "second", timestamp: "" },
    { id: "a2", role: "assistant", content: "reply two", timestamp: "" },
  ];
  actions.setSessionVenue("frontend");
  actions.setChatBackend({
    async chat() { return "unused"; },
    async *chatStream() {
      sends += 1;
      await ready;
      yield { delta: "reply two" };
      yield { transcript, done: true };
    },
  });
  try {
    actions.sendChatMessage("first");
    actions.sendChatMessage("second");
    await waitForChatCondition(() => calls.length === 1);
    finishTurn();
    await waitForChatCondition(() => !workspace.chatStreaming);
    // Queue verbs only: appended once, never popped, never resubmitted.
    // (Additive read-only calls on the same bridge — e.g. the tab-title
    // list_sessions refresh at turn end — do not count against this rail.)
    expect(calls.filter((c) => c.startsWith("session.queue"))).toEqual(["session.queue_remote:append"]);
    expect(sends).toBe(1);
    expect(workspace.pendingChatQueue).toEqual([]);
    expect(workspace.chatMessages.filter((row) => row.role !== "system").map((row) => row.content))
      .toEqual(["first", "reply one", "second", "reply two"]);
  } finally {
    finishTurn();
    request.mockRestore();
  }
});

it("interrupting a remote watcher does not clear the server queue", async () => {
  const request = spyOn(getCompanionBridge(), "request").mockResolvedValue({});
  actions.setSessionVenue("frontend");
  try {
    actions.interruptChat();
    await Bun.sleep(10);
    expect(request).not.toHaveBeenCalled();
  } finally {
    request.mockRestore();
  }
});

describe("chat", () => {
  it("starts with a welcome message", () => {
    expect(workspace.chatMessages.length).toBe(1);
    expect(workspace.chatMessages[0].role).toBe("assistant");
    expect(workspace.chatMessages[0].content).toContain("/help");
    expect(workspace.chatMessages[0].content).not.toMatch(/r4-cli|Companion|needs a live backend/i);
  });

  it("rewrites welcome when switching to demo mode", () => {
    actions.setDemoMode(true);
    expect(workspace.chatMessages[0].content).toMatch(/Demo/i);
    expect(workspace.chatMessages[0].content).not.toMatch(/needs a live backend/i);
  });

  it("appends a user message and eventually a reply", async () => {
    actions.sendChatMessage("hello");
    expect(workspace.chatMessages.length).toBe(2);
    expect(workspace.chatMessages[1].role).toBe("user");

    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(workspace.chatMessages.length).toBe(3);
    expect(workspace.chatMessages[2].role).toBe("assistant");
  });

  it("rotates fallback replies without inventing tasks or knowledge", async () => {
    actions.sendChatMessage("hello there");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const reply1 = workspace.chatMessages.at(-1)!.content;

    actions.sendChatMessage("upload my papers please");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const reply2 = workspace.chatMessages.at(-1)!.content;

    expect(reply1).not.toBe(reply2);
    expect(reply1).toContain("[Offline]");
    expect(reply2).toContain("[Offline]");

    const goalsBefore = workspace.goals.length;
    const tasksBefore = workspace.tasks.length;
    const knowledgeBefore = workspace.knowledge.length;

    actions.sendChatMessage("make a plan");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(workspace.chatMessages.at(-1)!.content).toContain("[Offline]");
    expect(workspace.goals.length).toBe(goalsBefore);
    expect(workspace.tasks.length).toBe(tasksBefore);

    actions.sendChatMessage("upload materials");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(workspace.chatMessages.at(-1)!.content).toContain("[Offline]");
    expect(workspace.knowledge.length).toBe(knowledgeBefore);
  });
});

describe("chat streaming", () => {
  it("streams deltas into a single assistant message with attribution", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { model: "test-model" };
        yield { delta: "Hello" };
        yield { delta: ", " };
        yield { delta: "world" };
        yield { done: true };
      },
    });

    const before = workspace.chatMessages.length;
    actions.sendChatMessage("stream please");
    // User message present synchronously; assistant added lazily on first delta.
    expect(workspace.chatMessages.length).toBe(before + 1);
    expect(workspace.chatMessages.at(-1)!.role).toBe("user");

    await new Promise((resolve) => setTimeout(resolve, 200));
    const last = workspace.chatMessages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.content).toBe("Hello, world");
    expect(last.model).toBe("test-model");
    expect(last.streaming).toBe(false);
    expect(workspace.chatStreaming).toBe(false);
  });

  it("prepends active-project context onto the wire message", async () => {
    let wireMessage = "";
    actions.setProject({
      id: "demo08-nl",
      name: "Demo 08 NL",
      description: "d",
      status: "active",
      goalIds: [],
    });
    actions.setKnowledge([
      { id: "k1", name: "batch-3-yields.csv", status: "ready", documentCount: 1 },
    ]);
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream(message: string) {
        wireMessage = message;
        yield { delta: "ok" };
        yield { done: true };
      },
    });

    actions.sendChatMessage("what files do I have?");
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(wireMessage).toContain("demo08-nl");
    expect(wireMessage).toContain("batch-3-yields.csv");
    expect(wireMessage).not.toContain("Do NOT call lab_catalog_search");
    expect(wireMessage).toContain("what files do I have?");
    // Visible user bubble stays clean (no context prefix).
    const userMsg = workspace.chatMessages.find((m) => m.role === "user");
    expect(userMsg?.content).toBe("what files do I have?");
  });

  it("flags fallback replies when the backend yields nothing", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      // eslint-disable-next-line require-yield
      async *chatStream() {
        return;
      },
    });

    actions.sendChatMessage("no backend output");
    await new Promise((resolve) => setTimeout(resolve, 200));
    const last = workspace.chatMessages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.fallback).toBe(true);
    expect(last.content.length).toBeGreaterThan(0);
  });

  it("interrupts an in-flight stream", async () => {
    let cancelled = false;
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream(_m, _h, _model, signal) {
        try {
          yield { model: "slow-model" };
          yield { delta: "partial" };
          for (let i = 0; i < 50; i++) {
            if (signal?.aborted) return;
            await new Promise((r) => setTimeout(r, 20));
            yield { delta: "." };
          }
        } finally {
          cancelled = true;
        }
      },
    });

    actions.sendChatMessage("long answer");
    await new Promise((resolve) => setTimeout(resolve, 60));
    actions.interruptChat();
    await new Promise((resolve) => setTimeout(resolve, 120));

    expect(workspace.chatStreaming).toBe(false);
    expect(cancelled).toBe(true);
    const last = workspace.chatMessages.at(-1)!;
    expect(last.streaming).toBe(false);
    // Interrupted before all 50 dots arrived.
    expect(last.content.length).toBeLessThan("partial".length + 50);
  });
});


describe("T5 topic-15: queued user messages behind a live turn", () => {
  it("a message sent mid-stream is marked Queued and lives at the queue tail", async () => {
    const backend = {
      chat: async () => {
        // simulate slow reply — stays streaming
        await new Promise((r) => setTimeout(r, 150));
        return "slow reply";
      },
    } as never;
    const { setWorkspace, workspace, actions } = await import("../src/stores");
    setWorkspace("chatBackend", backend);
    actions.sendChatMessage("first turn — slow reply");
    expect(workspace.chatStreaming).toBe(true);
    const baseline = workspace.chatMessages.length;
    actions.sendChatMessage("queued behind first");
    expect(workspace.pendingChatQueue.length).toBe(1);
    expect(workspace.chatMessages.at(-1)!.queued).toBe(true);
    await new Promise((r) => setTimeout(r, 400));
    // after the first turn closes, queue pops into a fresh turn
    expect(workspace.chatMessages.at(-1)!.role).toBe("assistant");
    expect(workspace.pendingChatQueue.length).toBe(0);
    setWorkspace("chatBackend", null);
  });

  it("chip surfaces in the header line for a queued message", async () => {
    const { workspace } = await import("../src/stores");
    const { actions } = await import("../src/stores");
    setWorkspace("chatBackend", null);
    setWorkspace("chatStreaming", true);
    actions.sendChatMessage("blocked behind stream");
    expect(workspace.chatMessages.at(-1)!.queued).toBe(true);
    expect(workspace.pendingChatQueue.length).toBeGreaterThan(0);
    // exit cleanup — interruptChat clears queue too
    actions.interruptChat();
    expect(workspace.pendingChatQueue.length).toBe(0);
    setWorkspace("chatStreaming", false);
  });
});
