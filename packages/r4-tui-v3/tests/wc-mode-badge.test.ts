/**
 * Wave C slice A — mode/permission state from bridge snapshots + the badge.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { mapBridgeStateToWorkspace, applyBridgeStatePatch } from "../src/bridge/map-state";

describe("mode/permission mapping from snapshot", () => {
  test("extracts interaction/permission modes and plan state", () => {
    const patch = mapBridgeStateToWorkspace({
      session: {
        model: "gpt-oss",
        permission_mode: "auto_safe",
        interaction_mode: "plan",
        plan_awaiting_confirmation: true,
        plan_pending_request: "refactor the auth layer",
      },
    }, undefined);
    expect(patch.interactionMode).toBe("plan");
    expect(patch.permissionMode).toBe("auto_safe");
    expect(patch.planAwaitingConfirmation).toBe(true);
    expect(patch.planPendingRequest).toBe("refactor the auth layer");
  });

  test("snapshots without session leave fields untouched", () => {
    const patch = mapBridgeStateToWorkspace({ model: "x" }, undefined);
    expect(patch.interactionMode).toBeUndefined();
    expect(patch.permissionMode).toBeUndefined();
    expect(patch.planAwaitingConfirmation).toBeUndefined();
  });
});

describe("badge semantics (mode change changes exactly the badge)", () => {
  beforeEach(() => {
    setWorkspace("interactionMode", "chat");
    setWorkspace("permissionMode", "ask_always");
    setWorkspace("planAwaitingConfirmation", false);
    setWorkspace("planPendingRequest", "");
  });

  test("apply updates modes and plan state", () => {
    actions.setInteractionModeState({
      interactionMode: "autopilot",
      permissionMode: "trust",
      planAwaitingConfirmation: false,
    });
    expect(workspace.interactionMode).toBe("autopilot");
    expect(workspace.permissionMode).toBe("trust");
    expect(workspace.planAwaitingConfirmation).toBe(false);
  });

  test("applyBridgeStatePatch forwards the mode state", () => {
    const patch = mapBridgeStateToWorkspace({
      session: { interaction_mode: "plan", permission_mode: "ask_always" },
    }, undefined);
    applyBridgeStatePatch(patch);
    expect(workspace.interactionMode).toBe("plan");
    expect(workspace.permissionMode).toBe("ask_always");
  });
});

describe("ADR-104 D-1: one mode badge", () => {
  test("autopilot names its trust source; other modes show the mode only", async () => {
    const { modeBadge } = await import("../src/lib/mode-badge");
    // ADR-104 amendment 1: plain autopilot by default; (policy) when an
    // organisation's allowlist narrows it. "legacy" was the old default.
    expect(modeBadge("autopilot", "server")).toBe("mode:autopilot(policy)");
    expect(modeBadge("autopilot", "local")).toBe("mode:autopilot");
    expect(modeBadge("autopilot", "legacy")).toBe("mode:autopilot");
    expect(modeBadge("autopilot", "")).toBe("mode:autopilot");
    expect(modeBadge("chat", "server")).toBe("mode:chat");
    expect(modeBadge("", "")).toBe("mode:chat");
  });

  test("an empty trust_source / plan_pending_request in a snapshot clears the stale value", () => {
    setWorkspace("trustSource", "server");
    setWorkspace("planPendingRequest", "old request");
    applyBridgeStatePatch(
      mapBridgeStateToWorkspace(
        { session: { interaction_mode: "chat", trust_source: "", plan_pending_request: "" } },
        undefined,
      ),
    );
    expect(workspace.trustSource).toBe("");
    expect(workspace.planPendingRequest).toBe("");
  });

  test("an empty interaction_mode is a missing value, not a clear", () => {
    setWorkspace("interactionMode", "plan");
    applyBridgeStatePatch(mapBridgeStateToWorkspace({ session: { interaction_mode: "" } }, undefined));
    expect(workspace.interactionMode).toBe("plan");
  });
});
