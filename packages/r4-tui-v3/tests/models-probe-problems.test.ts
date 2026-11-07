/**
 * /models probe-problem notices (field report 2026-10-20).
 *
 * A registered source whose /models probe fails (rejected key, wrong URL,
 * unreachable host) used to vanish silently: an empty picker then read
 * "(no provisioned models — use /endpoint up <model>)" — sending the user to
 * provision endpoints when the source credential was the problem. The bridge
 * now carries named probe problems; these pins assert the console names them
 * exactly where the bare "no provisioned models" line used to appear.
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setDemoMode } from "../src/lib/console-mode";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";

const KEY_REJECTED = "API key rejected — store a valid key";

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

/** Bridge stub answering list_models with the given payload. */
function stubBridge(listModels: Record<string, unknown>) {
  return {
    request: async (method: string) => {
      if (method === "list_models") return listModels;
      // A healthy gateway: an empty /models asks it why (slash-a#0).
      if (method === "list_stoppable_endpoints") return { endpoints: [] };
      throw new Error(`unexpected bridge call: ${method}`);
    },
    reconnect: async () => {},
  };
}

describe("/models surfaces named source-probe problems", () => {
  let savedBackend: string | undefined;

  beforeEach(() => {
    savedBackend = process.env.R4_TUI_BACKEND;
    delete process.env.R4_TUI_BACKEND;
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  afterEach(() => {
    setCompanionBridgeForTests(null);
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
  });

  test("an empty picker names the failed public source, not 'no provisioned models'", async () => {
    setCompanionBridgeForTests(
      stubBridge({
        models: [],
        current: "",
        problems: [{ source: "rikyu", error: KEY_REJECTED }],
      }) as never,
    );
    const result = await actions.dispatchSlashFromChat("/models");
    expect(result.kind).toBe("handled");
    expect(workspace.slashPickerOpen).toBe(false);
    expect(transcript()).toContain(`no models — rikyu: ${KEY_REJECTED}`);
    expect(transcript()).not.toContain("no provisioned models");
  });

  test("a picker that opens still warns about the sources that failed", async () => {
    setCompanionBridgeForTests(
      stubBridge({
        models: [{ name: "llama3.2@ollama", label: "llama3.2@ollama" }],
        current: "",
        problems: [{ source: "rikyu", error: KEY_REJECTED }],
      }) as never,
    );
    const result = await actions.dispatchSlashFromChat("/models");
    expect(result.kind).toBe("handled");
    expect(workspace.slashPickerOpen).toBe(true);
    expect(transcript()).toContain(`some sources failed their probe — rikyu: ${KEY_REJECTED}`);
    actions.closeAllOverlays();
    expect(workspace.slashPickerOpen).toBe(false);
  });

  test("an empty picker with healthy probes keeps the old guidance text", async () => {
    setCompanionBridgeForTests(
      stubBridge({ models: [], current: "", problems: [] }) as never,
    );
    const result = await actions.dispatchSlashFromChat("/models");
    expect(result.kind).toBe("handled");
    expect(workspace.slashPickerOpen).toBe(false);
    expect(transcript()).toContain("(no provisioned models — use /endpoint up <model> or /health)");
  });
});
