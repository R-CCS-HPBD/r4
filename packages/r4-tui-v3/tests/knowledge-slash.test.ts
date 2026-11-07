/**
 * /knowledge — status card when bare, a search when given a query.
 * The query used to be dropped for the status card (2026-10-17); a search
 * now rides dispatch_slash_command to the companion's K3 query facade.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { dispatchSlashCommand } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

function fakeBridge() {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  return {
    calls,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "dispatch_slash_command") {
        return { handled: true, output: [{ kind: "info", text: "  [1] MOF-5 notes  (score: 0.82)" }], state: {} };
      }
      return { project_id: "demo", graphs: [] };
    },
  };
}

async function run(line: string) {
  const bridge = fakeBridge();
  setCompanionBridgeForTests(bridge as never);
  try {
    return { result: await dispatchSlashCommand(line), calls: bridge.calls };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

describe("/knowledge dispatch", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  test("a query is searched, not swapped for the status card", async () => {
    const { result, calls } = await run("/knowledge MOF-5 capture");
    expect(result.kind).toBe("handled");
    expect(calls).toEqual([
      { method: "dispatch_slash_command", params: { command: "/knowledge MOF-5 capture" } },
    ]);
    expect(workspace.chatMessages.at(-1)!.content).toContain("MOF-5 notes");
  });

  test("bare, status and show keep the status card", async () => {
    for (const line of ["/knowledge", "/knowledge status", "/knowledge show"]) {
      const { calls } = await run(line);
      expect(calls.map((c) => c.method)).toEqual(["research_knowledge.status"]);
    }
  });

  test("detail keeps the detail card", async () => {
    const { calls } = await run("/knowledge detail");
    expect(calls.map((c) => c.method)).toEqual(["get_knowledge"]);
  });
});
