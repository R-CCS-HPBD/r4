/**
 * Client-side TTFT: first delta carries ttftMs → store keeps the LAST turn's
 * value; chip suffix formats compactly; absent on fallback/offline rounds.
 */
import { describe, it, expect, beforeEach } from "bun:test";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { formatTtftSuffix } from "../src/lib/cost-chip";

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("turn TTFT", () => {
  it("records the first delta's ttftMs and resets per turn", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { model: "qwen" };
        yield { delta: "first", ttftMs: 812 };
        yield { delta: " more" };
        yield { done: true };
      },
    });
    actions.sendChatMessage("hi");
    await new Promise((r) => setTimeout(r, 200));
    expect(workspace.lastTurnTtftMs).toBe(812);
  });

  it("stays null when no delta reports ttft (offline/fallback)", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { delta: "[Offline] reply", fallback: true, model: "offline" };
        yield { done: true };
      },
    });
    actions.sendChatMessage("hi");
    await new Promise((r) => setTimeout(r, 200));
    expect(workspace.lastTurnTtftMs).toBeNull();
  });

  it("formatTtftSuffix compact labels", () => {
    expect(formatTtftSuffix(null)).toBe("");
    expect(formatTtftSuffix(undefined)).toBe("");
    expect(formatTtftSuffix(812)).toBe(" · ttft 812ms");
    expect(formatTtftSuffix(2600)).toBe(" · ttft 2.6s");
  });
});
