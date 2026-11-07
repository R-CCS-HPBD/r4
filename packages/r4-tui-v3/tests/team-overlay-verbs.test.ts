/**
 * Keymap overlay verbs (B-family, 2026-09-27): the team overlay's lifecycle
 * verbs and selection ride the GLOBAL keymap with teamOverlay/execOverlay
 * gating — the overlay KeyTrap variant died with the composer focused.
 * Contract: handlers act on the open overlay; empty ids are inert no-ops.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";

describe("teamOverlayMoveCursor / toggleExpanded (store layer of the verbs)", () => {
  beforeEach(() => resetWorkspace());
  afterEach(() => resetWorkspace());

  it("cursor moves and never goes negative", () => {
    actions.teamOverlayMoveCursor(1);
    actions.teamOverlayMoveCursor(1);
    actions.teamOverlayMoveCursor(-1);
    expect(workspace.teamDetailCursor).toBe(1);
    actions.teamOverlayMoveCursor(-10);
    expect(workspace.teamDetailCursor).toBe(0);
  });

  it("toggle flips expanded on and off for the same role", () => {
    actions.teamOverlayToggleExpanded("pm");
    expect(workspace.teamDetailExpanded).toBe("pm");
    actions.teamOverlayToggleExpanded("pg");
    expect(workspace.teamDetailExpanded).toBe("pg");
    actions.teamOverlayToggleExpanded("pg");
    expect(workspace.teamDetailExpanded).toBeNull();
  });

  it("setTeamDetailId resets cursor/expansion for the new target", () => {
    actions.teamOverlayMoveCursor(3);
    actions.teamOverlayToggleExpanded("se");
    actions.setTeamDetailId("s2");
    expect(workspace.teamDetailCursor).toBe(0);
    expect(workspace.teamDetailExpanded).toBeNull();
  });
});

describe("esc sweep honours overlays with verbs like the modal layer (B4 + B-family)", () => {
  beforeEach(() => resetWorkspace());
  afterEach(() => resetWorkspace());

  it("one sweep clears the overlay only (pane alive)", async () => {
    const { overlayCloseSweep } = await import("../src/keymap");
    actions.openPane("work");
    actions.setTeamDetailId("s1");
    overlayCloseSweep();
    expect(workspace.teamDetailId).toBeNull();
    expect(workspace.paneView).toBe("work");
  });
});
