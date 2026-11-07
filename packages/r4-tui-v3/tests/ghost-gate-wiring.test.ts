/**
 * Ghost gate + completion tail read the WHOLE turn (2026-10-17).
 *
 * Each round's tool activity is pinned under its assistant segment and the
 * live accumulator emptied before the turn finishes. The gate read that
 * emptied accumulator, so it fired on every claim even when a file_write
 * ran, and the "✓ done — N actions" tail never appeared.
 */
import { describe, it, expect, beforeEach } from "bun:test";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { ActivityLine } from "../src/lib/tool-activity";

function tool(name: string): ActivityLine {
  return { status: "ok", text: `✓ ${name}`, name };
}

function noticesAfter(prompt: string, prefix: string) {
  const userIdx = workspace.chatMessages.findIndex((m) => m.role === "user" && m.content === prompt);
  return workspace.chatMessages
    .slice(userIdx + 1)
    .filter((m) => m.role === "system" && (m.noticeKey ?? "").startsWith(prefix));
}

function streamOf(chunks: Array<() => void | Record<string, unknown>>) {
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream() {
      for (const step of chunks) {
        const chunk = step();
        if (chunk) yield chunk;
      }
    },
  });
}

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("ghost gate wiring", () => {
  it("a write in an earlier round keeps a later 'I created' claim clean", async () => {
    streamOf([
      () => ({ model: "m" }),
      () => ({ delta: "Writing a.py." }),
      () => actions.setTurnToolActivity([tool("file_write")]),
      () => ({ roundBreak: true }),
      () => ({ delta: "I've created a.py." }),
      () => ({ done: true }),
    ]);
    actions.sendChatMessage("write a.py");
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(noticesAfter("write a.py", "ghost-gate:")).toEqual([]);
    const tail = noticesAfter("write a.py", "turn-done:");
    expect(tail).toHaveLength(1);
    expect(tail[0]!.content).toContain("1 action");
  });

  it("a claim with only read tools still raises the banner", async () => {
    streamOf([
      () => ({ model: "m" }),
      () => actions.setTurnToolActivity([tool("file_read")]),
      () => ({ delta: "I've updated config.yaml." }),
      () => ({ done: true }),
    ]);
    actions.sendChatMessage("update config");
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(noticesAfter("update config", "ghost-gate:")).toHaveLength(1);
  });

  it("a read-only status answer is not a claim", async () => {
    streamOf([
      () => ({ model: "m" }),
      () => actions.setTurnToolActivity([tool("workflow_status")]),
      () => ({ delta: "Your run wf-123 completed; nothing has changed since." }),
      () => ({ done: true }),
    ]);
    actions.sendChatMessage("is my run finished?");
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(noticesAfter("is my run finished?", "ghost-gate:")).toEqual([]);
  });
});
