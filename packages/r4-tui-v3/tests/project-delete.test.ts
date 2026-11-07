/**
 * /project delete (2026-10-01) pins: the TUI verb reaches the bridge
 * project_delete RPC; deleting THIS tab's selected project clears
 * workspace.project; deleting another tab's project leaves it.
 */

import { beforeEach, describe, expect, it, mock } from "bun:test";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let deleteReply: Record<string, unknown> = { ok: true, deleted: true, was_selected: true, config_changed: true };

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    ensureStarted: async () => undefined,
    on: () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "project_delete") return deleteReply;
      if (method === "project_current") return { current: null, project: null };
      return {};
    },
  }),
  getCompanionBridgeSnapshot: () => undefined,
}));
mock.module("../src/hooks/project-refresh", () => ({
  refreshAfterProjectConfigChange: async () => undefined,
  refreshProjectKnowledge: async () => undefined,
}));

import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

function project(id: string) {
  return { id, name: id, description: "", status: "active" as const, goalIds: [] };
}

describe("/project delete", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    deleteReply = { ok: true, deleted: true, was_selected: true, config_changed: true };
  });

  it("deleting the selected project clears workspace.project", async () => {
    setWorkspace("project", project("PRO_A"));
    await actions.dispatchSlashFromChat("/project delete PRO_A");
    const del = calls.find((c) => c.method === "project_delete");
    expect(del?.params.project_id).toBe("PRO_A");
    expect(workspace.project).toBeNull();
  });

  it("deleting another project keeps this tab's selection", async () => {
    deleteReply = { ok: true, deleted: true, was_selected: false, config_changed: false };
    setWorkspace("project", project("PRO_A"));
    await actions.dispatchSlashFromChat("/project delete PRO_B");
    expect(calls.find((c) => c.method === "project_delete")?.params.project_id).toBe("PRO_B");
    expect(workspace.project?.id).toBe("PRO_A");
  });

  it("bare /project delete prompts for the id instead of guessing", async () => {
    setWorkspace("project", project("PRO_A"));
    const result = await actions.dispatchSlashFromChat("/project delete");
    expect(calls.some((c) => c.method === "project_delete")).toBe(false);
    void result;
  });
});
