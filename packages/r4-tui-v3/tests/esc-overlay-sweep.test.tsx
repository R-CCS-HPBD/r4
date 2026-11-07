/**
 * B4 pin (2026-09-27): detail overlays (team/agent/endpoint/workflow-exec)
 * sit in the Esc modal sweep. Regression shape from the user report: with an
 * overlay open over the Work pane, Esc closed the PANE underneath and the
 * overlay was stranded forever. One sweep clears the overlay AND keeps the
 * pane alive.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { overlayCloseSweep } from "../src/keymap";
import { actions, resetWorkspace, workspace } from "../src/stores";

describe("Esc sweep with detail overlays (B4)", () => {
  beforeEach(() => resetWorkspace());
  afterEach(() => resetWorkspace());

  it("team detail over the work pane: sweep clears overlay, pane survives", () => {
    actions.openPane("work");
    actions.setTeamDetailId("s1");
    overlayCloseSweep();
    expect(workspace.teamDetailId).toBeNull();
    expect(workspace.paneView).toBe("work");
    overlayCloseSweep();
    expect(workspace.paneView).toBeNull();
  });

  it("agent/endpoint/exec overlays also count", () => {
    actions.openPane("work");
    actions.setAgentDetailId("a1");
    overlayCloseSweep();
    expect(workspace.agentDetailId).toBeNull();
    expect(workspace.paneView).toBe("work");

    actions.setEndpointDetailId("ep-1");
    overlayCloseSweep();
    expect(workspace.endpointDetailId).toBeNull();
    expect(workspace.paneView).toBe("work");

    actions.setExecDetailId("exec-1");
    overlayCloseSweep();
    expect(workspace.execDetailId).toBeNull();
    expect(workspace.paneView).toBe("work");
  });

  it("workflow definition card (2026-10-02): Esc closes the card, not the pane under it", () => {
    actions.openPane("work");
    actions.setWorkflowDefId("vision-pipe");
    overlayCloseSweep();
    expect(workspace.workflowDefId).toBeNull();
    expect(workspace.paneView).toBe("work");
  });
});
