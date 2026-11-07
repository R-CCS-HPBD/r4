/**
 * The chat cell's citation block gets the passages the answer cited (TUI
 * audit r2 #66, 2026-10-17): send_message's result carries `citations`, the
 * adapter yields them, and the store pins them on the reply.
 */
import { beforeEach, describe, expect, it } from "bun:test";
import { citationChunk } from "../src/adapters/companion-chat";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const ROW = {
  sid: "S1",
  artifact_id: "art-42",
  title: "mof5-capture.pdf",
  kind: "passage",
  preview: "MOF-5 adsorbs carbon dioxide strongly.",
};

describe("citationChunk", () => {
  it("maps the bridge rows onto the chat Citation shape", () => {
    expect(citationChunk([ROW])).toEqual({
      citations: [
        {
          artifactId: "art-42",
          kind: "passage",
          title: "[S1] mof5-capture.pdf",
          contentPreview: "MOF-5 adsorbs carbon dioxide strongly.",
          sid: "S1",
        },
      ],
    });
  });

  it("yields nothing when the check did not run", () => {
    expect(citationChunk(undefined)).toBeNull();
    expect(citationChunk([])).toBeNull();
  });
});

describe("citations reach the reply", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  it("a citations chunk after the answer pins them on the final reply", async () => {
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream() {
        yield { model: "m" };
        yield { delta: "MOF-5 adsorbs CO2 strongly [S1]." };
        yield citationChunk([ROW])!;
        yield { done: true };
      },
    });
    actions.sendChatMessage("does MOF-5 adsorb CO2?");
    await new Promise((resolve) => setTimeout(resolve, 200));
    const reply = workspace.chatMessages.findLast((m) => m.role === "assistant")!;
    expect(reply.content).toContain("[S1]");
    expect(reply.citations?.map((c) => c.artifactId)).toEqual(["art-42"]);
  });
});
