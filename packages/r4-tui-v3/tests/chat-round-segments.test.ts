/**
 * Round-segment ordering contract (store level).
 *
 * Long agent turns interleave model text with tool work. The transcript
 * must mirror invocation time — text₁, tools₁, text₂, tools₂ — with each
 * tool batch pinned UNDER the text round that triggered it, instead of one
 * merged reply with all tool details detached below it.
 */
import { describe, it, expect, beforeEach } from "bun:test";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { ActivityLine } from "../src/lib/tool-activity";

function line(text: string): ActivityLine {
  return { status: "ok", text };
}

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("chat round segments", () => {
  it("splits rounds into ordered segments with tool work pinned under the triggering text", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { model: "test-model" };
        yield { delta: "round one text" };
        // Tools complete after the round's text, before the next round.
        actions.setTurnToolActivity([line("✓ Ran ls"), line("✓ Read a.py")]);
        yield { roundBreak: true };
        yield { delta: "round two text" };
        actions.setTurnToolActivity([line("✓ Ran pytest")]);
        yield { done: true };
      },
    });

    actions.sendChatMessage("do work");
    await new Promise((resolve) => setTimeout(resolve, 200));

    // user + two assistant segments, in invocation-time order. Content-
    // anchored (not absolute tail positions): sibling files' async tails may
    // interleave their own messages into the window — the contract is the
    // order of THESE items, never "the last three rows".
    const userIdx = workspace.chatMessages.findIndex(
      (m) => m.role === "user" && m.content === "do work",
    );
    expect(userIdx).toBeGreaterThanOrEqual(0);
    const seg1 = workspace.chatMessages.find(
      (m, i) => i > userIdx && m.content === "round one text",
    )!;
    const seg2 = workspace.chatMessages.find(
      (m, i) => i > userIdx && m.content === "round two text",
    )!;
    expect(seg1).toBeDefined();
    expect(seg2).toBeDefined();
    expect(workspace.chatMessages.indexOf(seg2)).toBeGreaterThan(
      workspace.chatMessages.indexOf(seg1),
    );

    expect(seg1.role).toBe("assistant");
    expect(seg1.content).toBe("round one text");
    expect(seg1.streaming).toBe(false);
    expect(seg1.model).toBe("test-model");
    expect(seg1.toolCalls?.map((l) => l.text)).toEqual(["✓ Ran ls", "✓ Read a.py"]);

    expect(seg2.role).toBe("assistant");
    expect(seg2.content).toBe("round two text");
    expect(seg2.streaming).toBe(false);
    expect(seg2.model).toBe("test-model");
    expect(seg2.toolCalls?.map((l) => l.text)).toEqual(["✓ Ran pytest"]);

    // Nothing left in the live accumulator; no synthetic separators in text.
    expect(workspace.turnToolActivity).toEqual([]);
    expect(seg1.content).not.toContain("\n\n");
    expect(seg2.content).not.toContain("\n\n");

    // Group expand details registered for both segments (B1 grammar).
    expect(workspace.noticeDetails[`group:${seg1.id}`]).toEqual(["✓ Ran ls", "✓ Read a.py"]);
    expect(workspace.noticeDetails[`group:${seg2.id}`]).toEqual(["✓ Ran pytest"]);
  });

  it("ignores a round break with no open segment (no empty cells)", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { roundBreak: true };
        yield { delta: "only text" };
        yield { done: true };
      },
    });

    actions.sendChatMessage("hi");
    await new Promise((resolve) => setTimeout(resolve, 200));

    const assistants = workspace.chatMessages.filter((m) => m.role === "assistant");
    // welcome + one segment — the spurious leading break created nothing.
    expect(assistants).toHaveLength(2);
    expect(assistants.at(-1)!.content).toBe("only text");
  });

  it("closes cleanly when a trailing round break ends the stream (textless final round)", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { model: "test-model" };
        yield { delta: "final answer" };
        actions.setTurnToolActivity([line("✓ Ran tail")]);
        yield { roundBreak: true };
        yield { done: true };
      },
    });

    actions.sendChatMessage("wrap up");
    await new Promise((resolve) => setTimeout(resolve, 200));

    const lastAssistant = workspace.chatMessages.findLast((m) => m.role === "assistant")!;
    expect(lastAssistant.content).toBe("final answer");
    expect(lastAssistant.streaming).toBe(false);
    expect(lastAssistant.model).toBe("test-model");
    expect(lastAssistant.toolCalls?.map((l) => l.text)).toEqual(["✓ Ran tail"]);
    expect(workspace.turnToolActivity).toEqual([]);
    expect(workspace.chatStreaming).toBe(false);
    // The completion tail counts the pinned tail-round tool (it read the
    // emptied accumulator before 2026-10-17 and never showed).
    const tail = workspace.chatMessages.at(-1)!;
    expect(tail.role).toBe("system");
    expect(tail.content).toContain("1 action");
  });
});
