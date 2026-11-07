/**
 * Per-tab projects (2026-10-01) pins:
 * - a fresh tab inherits the previous tab's project (badge + spawn env);
 * - sibling tabs keep independent projects through the snapshot swap;
 * - each tab's bridge boots with its own R4_PROJECT_ID so a /project
 *   switch in tab A can never leak into tab B (bridge-side selection is
 *   process-local — see r4/companion/ui/project_bridge.py).
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import {
  bridgeForTab,
  registerBridgeAttacher,
  resetTabsRegistryForTests,
} from "../src/tabs/registry";

function project(id: string) {
  return { id, name: id, description: "", status: "active" as const, goalIds: [] };
}

function envExtraOf(bridge: unknown): Record<string, string> | undefined {
  return (bridge as { envExtra?: Record<string, string> }).envExtra;
}

describe("per-tab project selection", () => {
  beforeEach(() => {
    resetWorkspace();
    resetTabsRegistryForTests();
  });

  it("a fresh tab inherits the previous tab's project", () => {
    const first = workspace.chatTabs[0]!.id;
    setWorkspace("project", project("PRO_A"));
    setWorkspace("identityProjectId", "PRO_A");
    const second = actions.tabCreate();
    expect(second).toBeTruthy();
    expect(workspace.activeChatTabId).toBe(second ?? "never");
    expect(workspace.project?.id).toBe("PRO_A");
    expect(workspace.identityProjectId).toBe("PRO_A");
    // …and activating back still shows A's values (symmetric swap).
    actions.tabActivate(first);
    expect(workspace.project?.id).toBe("PRO_A");
  });

  it("sibling tabs keep independent projects through the snapshot swap", () => {
    const first = workspace.chatTabs[0]!.id;
    setWorkspace("project", project("PRO_A"));
    const second = actions.tabCreate();
    setWorkspace("project", project("PRO_B"));
    setWorkspace("identityProjectId", "PRO_B");
    actions.tabActivate(first);
    expect(workspace.project?.id).toBe("PRO_A");
    actions.tabActivate(second!);
    expect(workspace.project?.id).toBe("PRO_B");
    expect(workspace.identityProjectId).toBe("PRO_B");
  });

  it("each tab's bridge boots with its own R4_PROJECT_ID", () => {
    registerBridgeAttacher(() => undefined);
    const first = workspace.chatTabs[0]!.id;
    setWorkspace("project", project("PRO_A"));
    expect(envExtraOf(bridgeForTab(first))?.R4_PROJECT_ID).toBe("PRO_A");
    const second = actions.tabCreate();
    setWorkspace("project", project("PRO_B"));
    expect(envExtraOf(bridgeForTab(second!))?.R4_PROJECT_ID).toBe("PRO_B");
  });

  it("a background tab's pending bridge env comes from its snapshot", () => {
    registerBridgeAttacher(() => undefined);
    const first = workspace.chatTabs[0]!.id;
    setWorkspace("project", project("PRO_A"));
    const second = actions.tabCreate();
    setWorkspace("project", project("PRO_B"));
    // Snapshot B out by re-activating A, then mint B's bridge while B is
    // backgrounded (the registry path for late bridge creation).
    actions.tabActivate(first);
    expect(envExtraOf(bridgeForTab(second!))?.R4_PROJECT_ID).toBe("PRO_B");
  });
});
