/**
 * /workflow on|off — ADR-075 G2 session arm: dispatch → bridge verb → mapped
 * workspace flag + chip-visible state. While armed, submit guards refuse the
 * non-UMRS lane by name (python pins: r4/tests/test_workflow_arm.py).
 */
import { describe, test, expect, beforeEach, mock } from "bun:test";
import { dispatchSlashCommand } from "../src/commands/slash";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { mapBridgeStateToWorkspace } from "../src/bridge/map-state";

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));

// Sibling files mock the "../bridge" barrel process-globally; slash.ts's
// getCompanionBridge resolves through it. Register ours the same way so the
// dispatch path lands on THIS fake regardless of loader order.
type FakeBridge = {
  calls: Array<{ method: string; params: Record<string, unknown> }>;
  request: (method: string, params: Record<string, unknown>) => Promise<unknown>;
};
let currentBridge: FakeBridge | null = null;
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => currentBridge,
}));

function fakeBridge(reply: Record<string, unknown>): FakeBridge {
  const calls: FakeBridge["calls"] = [];
  const bridge: FakeBridge = {
    calls,
    request: async (method, params) => {
      calls.push({ method, params });
      return reply;
    },
  };
  currentBridge = bridge;
  return bridge;
}

describe("/workflow on|off (ADR-075 G2)", () => {
  beforeEach(() => {
    resetWorkspace();
    actions.closeAllOverlays();
    currentBridge = null;
  });

  test("on sends set_workflow_armed armed:true and maps the reply state", async () => {
    const bridge = fakeBridge({
      workflow_armed: true,
      state: { session: { workflow_armed: true } },
    });
    const result = await dispatchSlashCommand("/workflow on");
    expect(result.kind).toBe("handled");
    // Async tails from sibling files may fire extra requests through the
    // installed fake — assert presence, never exact-call exclusivity.
    expect(bridge.calls).toContainEqual({
      method: "set_workflow_armed",
      params: { armed: true },
    });
    expect(workspace.workflowArmed).toBe(true);
    const notice = workspace.chatMessages.find((m) =>
      m.content.includes("workflow arm ON"),
    )?.content;
    expect(notice).toContain("UMRS durable lane");
  });

  test("off lifts the arm and the notice names default routing", async () => {
    const bridge = fakeBridge({
      workflow_armed: false,
      state: { session: { workflow_armed: false } },
    });
    const result = await dispatchSlashCommand("/workflow off");
    expect(result.kind).toBe("handled");
    expect(bridge.calls).toContainEqual({
      method: "set_workflow_armed",
      params: { armed: false },
    });
    expect(workspace.workflowArmed).toBe(false);
    expect(
      workspace.chatMessages.find((m) => m.content.includes("workflow arm off"))?.content,
    ).toContain("arm off");
  });

  test("map-state carries workflow_armed only when present", () => {
    const patchWith = mapBridgeStateToWorkspace(
      { session: { workflow_armed: true } } as never,
      undefined,
    ) as unknown as Record<string, unknown>;
    expect(patchWith.workflowArmed).toBe(true);
    const patchWithout = mapBridgeStateToWorkspace(
      { session: {} } as never,
      undefined,
    ) as unknown as Record<string, unknown>;
    expect("workflowArmed" in patchWithout).toBe(false);
  });
});
