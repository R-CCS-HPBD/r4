/**
 * B1 pin (2026-09-27): /project switch refreshes the badge immediately —
 * the handler pulls a fresh snapshot after the switch so
 * `identityProjectId` follows the new project without waiting for a turn.
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    ensureStarted: async () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "project_switch") return { ok: true, config_changed: true };
      if (method === "get_state") {
        return {
          config: { project_id: "proj-b", profile: "default", gateway_url: "g", source_name: "" },
          session: { id: "s1", model: "m" },
          skills: [],
          usage: {},
          messages: [],
        };
      }
      if (method === "project_current") return { current: "default" };
      return {};
    },
  }),
  getCompanionBridgeSnapshot: () => undefined,
}));
mock.module("../src/hooks/project-refresh", () => ({
  refreshAfterProjectConfigChange: async () => undefined,
  refreshProjectKnowledge: async () => undefined,
}));
mock.module("../src/hooks/useBackend", () => ({
  loadWorkspace: async () => undefined,
}));
mock.module("../src/hooks/useKnowledge", () => ({
  createKnowledgePoller: () => undefined,
  stopKnowledgePoller: () => undefined,
}));

describe("/project switch badge", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
  });

  afterEach(() => resetWorkspace());

  it("identityProjectId follows the switch (B1)", async () => {
    await actions.dispatchSlashFromChat("/project switch proj-b");
    const methods = calls.map((c) => c.method);
    expect(methods).toContain("project_switch");
    expect(methods).toContain("get_state");
    expect(workspace.identityProjectId).toBe("proj-b");

  });
});
