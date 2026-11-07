/**
 * Per-tab projects (2026-10-01) pin: refreshAfterProjectConfigChange must
 * take "current project" from the ACTIVE TAB's bridge (process-local
 * selection), never from the CLI `r4 project list` global default — the
 * latter leaks another tab's / the terminal's selection into this tab.
 *
 * The bridge is injected via setCompanionBridgeForTests (no module mock:
 * bun overlaps async tests across files, and a module mock here would
 * poison sibling files' bridge imports).
 */

import { afterEach, beforeEach, describe, expect, it, mock } from "bun:test";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";

const bridgeRequests: string[] = [];
let bridgeCurrent: { current?: string | null; project?: { project_id: string; name?: string } | null } = {};
let bridgeFails = false;

const fakeBridge = {
  request: async (method: string) => {
    bridgeRequests.push(method);
    if (bridgeFails) throw new Error("bridge down");
    if (method === "project_current") return bridgeCurrent;
    return {};
  },
} as unknown as CompanionBridgeClient;

let loadWorkspaceCalls = 0;
let pollerStarts = 0;
let pollerStops = 0;

mock.module("../src/hooks/useBackend", () => ({
  loadWorkspace: async () => {
    loadWorkspaceCalls += 1;
  },
}));

mock.module("../src/hooks/useKnowledge", () => ({
  createKnowledgePoller: () => {
    pollerStarts += 1;
  },
  stopKnowledgePoller: () => {
    pollerStops += 1;
  },
}));

import { resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { refreshAfterProjectConfigChange } from "../src/hooks/project-refresh";

describe("refreshAfterProjectConfigChange (tab-scoped)", () => {
  beforeEach(() => {
    resetWorkspace();
    // Live-backend posture: a named source so the poller leg is exercised.
    setWorkspace("dataSource", { name: "r4-cli" } as never);
    setCompanionBridgeForTests(fakeBridge);
    bridgeRequests.length = 0;
    bridgeCurrent = {};
    bridgeFails = false;
    loadWorkspaceCalls = 0;
    pollerStarts = 0;
    pollerStops = 0;
  });

  afterEach(() => setCompanionBridgeForTests(null));

  it("sets workspace.project from the active tab's bridge, skipping the CLI read", async () => {
    bridgeCurrent = { current: "PRO_B", project: { project_id: "PRO_B", name: "Project B" } };
    await refreshAfterProjectConfigChange();
    expect(bridgeRequests).toContain("project_current");
    expect(loadWorkspaceCalls).toBe(0);
    expect(workspace.project?.id).toBe("PRO_B");
    expect(workspace.project?.name).toBe("Project B");
    expect(pollerStops).toBe(1);
    expect(pollerStarts).toBe(1);
  });

  // TUI audit r2 #48: a bridge that answers "no project" is the tab's own
  // truth — the global CLI snapshot would paint another tab's selection.
  it("keeps the tab project-less when the bridge answers with no project", async () => {
    bridgeCurrent = { current: null, project: null };
    await refreshAfterProjectConfigChange();
    expect(loadWorkspaceCalls).toBe(0);
    expect(workspace.project).toBeNull();
  });

  it("falls back to the CLI snapshot when the bridge is unreachable", async () => {
    bridgeFails = true;
    await refreshAfterProjectConfigChange();
    expect(loadWorkspaceCalls).toBe(1);
  });
});
