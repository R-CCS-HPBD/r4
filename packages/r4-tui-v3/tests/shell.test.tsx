/**
 * Shell layout tests.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { Shell } from "../src/layout/shell";
import { resetWorkspace, actions, workspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
});

describe("Shell", () => {
  it("renders all zones", async () => {
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // V4: chat primary + status line; nav rail and old console zones are gone.
    expect(frame).toContain("Chat with R4");
    expect(frame).toContain("Message R4");
    // Wave E: identity anchors the left status segment (acct/project · agents).
    expect(frame).toContain("? · agents");
    expect(frame).not.toContain("Command Center");
    setup.renderer.destroy();
  });

  it("identity chip shows account@project and the caller's circles (ADR-086)", async () => {
    // user report 2026-10-06: the fields were stored but never rendered
    actions.setIdentityState({ username: "mnagaso", projectId: "lab" });
    actions.setIdentityCircles(["lab/proj-b", "lab/team"]);
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("mnagaso@lab ○lab/proj-b +1 · agents");
    setup.renderer.destroy();
  });

  it("identity chip names the credential state without an account (2026-10-07)", async () => {
    // "default/{project}" masqueraded the profile as an account — the no-JWT
    // states are named instead: key-only(profile) / signed-out.
    actions.setIdentityState({ projectId: "r4-dev", profile: "default", credential: "api-key" });
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("r4-dev · key-only(default) · agents");
    setup.renderer.destroy();
  });

  it("identity chip says signed-out with no credentials at all", async () => {
    actions.setIdentityState({ projectId: "r4-dev", profile: "default", credential: "none" });
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("r4-dev · signed-out · agents");
    setup.renderer.destroy();
  });

  it("identity chip keeps the legacy shape only for pre-field bridges", async () => {
    // credential "" = snapshot predates the field (stale bridge) — the one
    // case the old ambiguous profile/project shape still renders.
    actions.setIdentityState({ projectId: "lab" });
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("default/lab · agents");
    setup.renderer.destroy();
  });

  it("keeps account/project/circle visible while a turn is live (user report 2026-10-06)", async () => {
    // The live work indicator owns the left segment during a turn; without
    // the right-side mirror the identity vanished for the whole generation.
    actions.setIdentityState({ username: "mnagaso", projectId: "lab" });
    actions.setIdentityCircles(["lab/proj-b", "lab/team"]);
    actions.setChatTurnStatus("selecting tools");
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("selecting tools"); // live work indicator (left)
    expect(frame).toContain("mnagaso@lab ○lab/proj-b +1"); // mirrored chip (right)
    setup.renderer.destroy();
    actions.setChatTurnStatus(null);
  });

  it("no identity chip without a project id (no fabricated account)", async () => {
    actions.setChatTurnStatus("selecting tools");
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).not.toContain("default/?");
    setup.renderer.destroy();
    actions.setChatTurnStatus(null);
  });

  it("switches to knowledge space", async () => {
    actions.setSpace("knowledge");
    expect(workspace.selectedNodeId).toBe("knowledge");
    // Seed a catalog row so F4 renders the knowledge UI (not the empty hint).
    actions.setKnowledge([
      { id: "k-1", name: "batch-3-yields.csv", status: "ready", documentCount: 1 },
    ]);
    // V4: the knowledge view lives in the detail pane.
    actions.openPane("knowledge");
    const setup = await testRender(() => <Shell />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Knowledge");
    expect(frame).toContain("batch-3-yields.csv");
    setup.renderer.destroy();
  });
});
