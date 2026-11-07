/**
 * Stabilization lane `focus` (2026-10-21), pane and tray keys: sessions
 * pane `r` (full id, keys handed to the composer), the /tour keys, the
 * Activity tray keys (F9 / Ctrl+T show the /logs pane), and the venue
 * picker's rows at 80x24.
 */
import { describe, it, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, activeTab, setWorkspace, workspace } from "../src/stores";
import { VenuePickerOverlay } from "../src/components/venue-picker-overlay";
import { tourStepCount } from "../src/lib/tour-steps";
import { installStabHarness, mountApp, recordingBridge, settle } from "./helpers/stab-focus-harness";

installStabHarness();

// ── sessions pane r ────────────────────────────────────────────────────────

describe("sessions pane r (rename)", () => {
  const record = {
    id: "7d84512e-1111-2222-3333-444455556666",
    name: "alpha one",
    model: "m",
    created_at: "",
    updated_at: "",
    pinned: false,
    archived_at: "",
    venue: "local",
    state: "",
    sync: "",
  };

  it("seeds the FULL id and hands the keys to the composer (d no longer archives)", async () => {
    const calls: string[] = [];
    const setup = await mountApp();
    recordingBridge(calls);
    actions.openPane("sessions");
    await settle(setup);
    setWorkspace("sessionsList", [record] as never);
    setWorkspace("sessionsCursor", 0);
    setWorkspace("paneFocus", true);
    await settle(setup);
    setup.mockInput.pressKey("r");
    await settle(setup, 150);
    expect(workspace.paneFocus).toBe(false);
    expect(activeTab().chatDraft).toBe(`/sessions rename ${record.id} `);
    await setup.mockInput.typeText("dn");
    await settle(setup);
    expect(calls.filter((c) => c.includes("archive"))).toEqual([]);
    expect(workspace.venuePickerOpen).toBe(false);
    expect(activeTab().chatDraft).toBe(`/sessions rename ${record.id} dn`);
    setup.renderer.destroy();
  });
});

// ── (d) tour, Activity tray keys, venue picker rows ────────────────────────

describe("tour keys (d)", () => {
  it("n on the last step closes the tour, as its footer says", async () => {
    const setup = await mountApp();
    actions.openTour(tourStepCount() - 1);
    await settle(setup);
    expect(setup.captureCharFrame()).toContain("Enter/n = close tour");
    setup.mockInput.pressKey("n");
    await settle(setup);
    expect(workspace.tourOpen).toBe(false);
    setup.renderer.destroy();
  });

  it("Enter on a step without a command advances instead of closing", async () => {
    const setup = await mountApp();
    actions.openTour(0);
    await settle(setup);
    setup.mockInput.pressEnter();
    await settle(setup);
    expect([workspace.tourOpen, workspace.tourStep]).toEqual([true, 1]);
    setup.renderer.destroy();
  });
});

describe("Activity tray keys (d)", () => {
  it("F9 shows the tray (the /logs pane) and hides it again", async () => {
    const setup = await mountApp();
    setup.mockInput.pressKey("F9");
    await settle(setup);
    expect(workspace.paneView).toBe("logs");
    expect(workspace.paneFocus).toBe(false);
    setup.mockInput.pressKey("F9");
    await settle(setup);
    expect(workspace.paneView).toBeNull();
    setup.renderer.destroy();
  });

  it("Ctrl+T shows the to-do checklist (Plan tab) on screen", async () => {
    const setup = await mountApp();
    setup.mockInput.pressKey("t", { ctrl: true });
    await settle(setup);
    expect([workspace.paneView, workspace.activityTab]).toEqual(["logs", "todos"]);
    setup.renderer.destroy();
  });
});

describe("venue picker rows (d)", () => {
  it("80x24: every option row is intact (no overlapped rows)", async () => {
    const setup = await testRender(() => <VenuePickerOverlay />, { width: 80, height: 24 });
    await settle(setup, 120);
    const frame = setup.captureCharFrame();
    expect(frame).toMatch(/\[l\] Local/);
    expect(frame).toMatch(/\[c\] Cluster/);
    expect(frame).toContain("runs on this machine");
    setup.renderer.destroy();
  });
});
