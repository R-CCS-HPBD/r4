/**
 * Interrupts are honest in the transcript (stabilization sweep 2026-10-21).
 *
 * chat#9: a mid-stream Esc left the partial reply looking like a finished
 * answer. chat#2: an Esc right after Enter left a bare user row. chat#3: Esc
 * with prompts queued turned the never-sent bubbles into plain "sent" rows.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { ChatStreamChunk } from "../src/adapters/types";

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function until(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 1000;
  while (!condition() && Date.now() < deadline) await Bun.sleep(5);
  expect(condition()).toBe(true);
}

/** A backend that streams `first` chunks, then waits for the abort. */
function streamingUntilAborted(first: ChatStreamChunk[]) {
  return {
    async chat() {
      return "unused";
    },
    async *chatStream(_m: string, _h: unknown, _model: unknown, signal?: AbortSignal) {
      for (const chunk of first) yield chunk;
      while (!signal?.aborted) await Bun.sleep(5);
    },
  };
}

const rows = () => workspace.chatMessages.filter((m) => m.role !== "system");

describe("interrupt marks the turn stopped", () => {
  it("a partial reply ends with the stopped marker", async () => {
    actions.setChatBackend(streamingUntilAborted([{ model: "m" }, { delta: "w0 w1 w2" }]) as never);
    actions.sendChatMessage("go");
    await until(() => rows().at(-1)?.content === "w0 w1 w2");
    actions.interruptChat();
    await until(() => !workspace.chatStreaming && rows().at(-1)?.streaming === false);
    await until(() => rows().at(-1)?.content.endsWith("(Generation stopped.)") === true);
    expect(rows().at(-1)!.content).toBe("w0 w1 w2\n\n(Generation stopped.)");
  });

  it("an interrupt before any text leaves a stopped row, not a bare user row", async () => {
    actions.setChatBackend(streamingUntilAborted([]) as never);
    actions.sendChatMessage("go early");
    await Bun.sleep(20);
    actions.interruptChat();
    await until(() => rows().at(-1)?.role === "assistant");
    expect(rows().map((m) => [m.role, m.content])).toContainEqual(["assistant", "(Generation stopped.)"]);
    expect(rows().at(-2)?.content).toBe("go early");
  });
});

describe("interrupt drops queued prompts visibly", () => {
  it("removes the never-sent bubbles, hands them back, and says so", async () => {
    actions.setChatBackend(streamingUntilAborted([{ delta: "busy" }]) as never);
    actions.sendChatMessage("go");
    await until(() => rows().at(-1)?.content === "busy");
    actions.sendChatMessage("qone");
    actions.sendChatMessage("qtwo");
    expect(workspace.pendingChatQueue.length).toBe(2);
    actions.interruptChat();
    await until(() => !workspace.chatStreaming);
    const contents = rows().map((m) => m.content);
    expect(contents).not.toContain("qone");
    expect(contents).not.toContain("qtwo");
    expect(workspace.pendingChatQueue).toEqual([]);
    expect(workspace.pendingChatDraft).toContain("qone");
    expect(workspace.pendingChatDraft).toContain("qtwo");
    const notices = workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);
    expect(notices.some((n) => n.includes("2 queued prompts not sent"))).toBe(true);
  });
});
