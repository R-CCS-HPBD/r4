/**
 * /decision-model — ADR-076 decision model toggle in the TUI.
 * Local row (visible without the companion catalog) + dispatch → the Python
 * registry via dispatch_slash_command (one implementation of the behaviour).
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { dispatchSlashCommand, filterSlashCommands, localSlashCommands } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

function fakeBridge(text: string) {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  return {
    calls,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return { handled: true, output: [{ kind: "system", text }], state: {} };
    },
  };
}

describe("/decision-model row", () => {
  test("is a local command with on/off/status hints", () => {
    const row = localSlashCommands().find((e) => e.name === "decision-model");
    expect(row).toBeDefined();
    expect(row!.aliases).toContain("decision");
    const subs = filterSlashCommands(localSlashCommands(), "/decision-model").map((e) => e.name);
    expect(subs).toEqual(["decision-model on", "decision-model off", "decision-model status"]);
  });
});

describe("/decision-model dispatch", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  async function run(line: string, text = "decision model: on — Bespoke-Nimble-9B (served by the platform)") {
    const bridge = fakeBridge(text);
    setCompanionBridgeForTests(bridge as never);
    try {
      return { result: await dispatchSlashCommand(line), calls: bridge.calls };
    } finally {
      setCompanionBridgeForTests(null);
    }
  }

  test("on forwards the canonical line and prints the bridge's answer", async () => {
    const { result, calls } = await run("/decision-model on");
    expect(result.kind).toBe("handled");
    expect(calls).toEqual([
      { method: "dispatch_slash_command", params: { command: "/decision-model on" } },
    ]);
    expect(workspace.chatMessages.at(-1)!.content).toContain("Bespoke-Nimble-9B");
  });

  test("the /decision alias and a named model keep the canonical name", async () => {
    const { calls } = await run("/decision on Other-Judge-1B");
    expect(calls[0]!.params).toEqual({ command: "/decision-model on Other-Judge-1B" });
  });

  test("bare command asks for status; off forwards off", async () => {
    expect((await run("/decision-model", "decision model: off")).calls[0]!.params).toEqual({
      command: "/decision-model",
    });
    expect((await run("/decision-model off", "decision model: off")).calls[0]!.params).toEqual({
      command: "/decision-model off",
    });
  });

  test("an unknown verb is a usage error and never reaches the bridge", async () => {
    const { result, calls } = await run("/decision-model maybe");
    expect(result.kind).toBe("error");
    expect(calls).toEqual([]);
  });
});
