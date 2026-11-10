/**
 * Stabilization lane `focus` (2026-10-21), overlays: the composer yields to
 * EVERY keyboard-owning overlay so its close is a focus edge (the /connect
 * wizard and the venue / remote-source pickers left the composer dead after
 * Esc), and Esc on Help / the palette / a picker / an agent question closes
 * that overlay only — never the running turn, its queue or the docked pane.
 */
import { describe, it, expect } from "bun:test";
import { actions, activeTab, setWorkspace, workspace } from "../src/stores";
import { chatInputFocused } from "../src/lib/chat-input-focus";
import { keyboardLayerOwners, STORE_LAYERS } from "../src/keymap/store-claims";
import { CLAIM_RANK } from "../src/keymap/key-claims";
import { installStabHarness, mountApp, recordingBridge, settle } from "./helpers/stab-focus-harness";

installStabHarness();

// ── (a) the composer yields to every keyboard-owning overlay ───────────────

/** How to open each keyboard-owning store layer. A new layer in
 * keymap/store-claims.ts fails the coverage pin until it is listed here. */
const OPENERS: Record<string, () => void> = {
  splash: () => actions.openSplash(),
  tour: () => actions.openTour(0),
  "remote-source-picker": () => actions.openRemoteSourcePicker("start"),
  "venue-picker": () => actions.openVenuePicker(),
  "web-access": () => actions.openWebAccessPrompt({ purpose: "slash", remote: false }),
  "search-server-offer": () => actions.openSearchServerOffer(),
  "command-palette": () => actions.openCommandPalette(),
  "slash-bar": () => actions.openSlashBar(),
  "slash-picker": () => actions.openSlashPicker({ title: "t", items: [] } as never),
  help: () => actions.openHelp(),
  "quit-dialog": () => setWorkspace("quitDialogOpen", true),
  "tool-approval": () => actions.openToolApproval({ id: "t1", tool: "bash", options: ["once", "reject"] }),
  "ask-user": () => actions.openAskUser({ id: "q1", question: "pick", options: ["a"] }),
  "list-overlay": () => setWorkspace("listOverlay", { kind: "skills" } as never),
  "start-research": () => actions.openStartResearch("create"),
  "connect-wizard": () => actions.openConnectWizard("login"),
  "start-actions-menu": () => actions.openStartActionsMenu(),
  "rewind-menu": () => setWorkspace("rewindMenuOpen", true),
  "subagent-inspector": () => actions.openSubagentInspector({
    sessionId: "child-1",
    agentId: "agent-1",
    parentSessionId: "parent-1",
    parentAgentId: "parent-agent",
    venue: "local",
  }),
};

/** Store layers above the composer that own NO typed input (Esc / verbs
 * only): the composer keeps focus under them, as before. */
const NOT_KEYBOARD_OWNING = new Set([
  "btw-overlay", // handled explicitly in chatInputFocused
  "citation-preview", // K1.4; handled explicitly in chatInputFocused, like /btw
  "codeview-revert-arm",
  "team-detail",
  "agent-detail",
  "endpoint-detail",
  "exec-detail",
  "workflow-def",
]);

/** Every `…Open` flag in the workspace, classified: the layer that owns
 * the keyboard while it is set, or why it never takes the keys. */
const OPEN_FLAGS: Record<string, string> = {
  commandPaletteOpen: "command-palette",
  slashBarOpen: "slash-bar",
  slashPickerOpen: "slash-picker",
  tourOpen: "tour",
  startActionsMenuOpen: "start-actions-menu",
  rewindMenuOpen: "rewind-menu",
  helpOpen: "help",
  quitDialogOpen: "quit-dialog",
  splashOpen: "splash",
  venuePickerOpen: "venue-picker",
  remoteSourcePickerOpen: "remote-source-picker",
  activityOpen: "-- no surface of its own (the tray is the /logs pane)",
  contextPanelOpen: "-- a docked panel, never a key owner",
  notificationCenterOpen: "-- no component renders it",
};

describe("composer focus covers every overlay (a)", () => {
  it("every store layer above the composer is classified", () => {
    const above = STORE_LAYERS.filter((l) => l.rank > CLAIM_RANK.local && l.rank !== CLAIM_RANK.pane);
    for (const layer of above) {
      const known = layer.owner in OPENERS || NOT_KEYBOARD_OWNING.has(layer.owner);
      expect(`${layer.owner}: ${known}`).toBe(`${layer.owner}: true`);
    }
    expect(new Set(keyboardLayerOwners())).toEqual(new Set(Object.keys(OPENERS)));
  });

  for (const [owner, open] of Object.entries(OPENERS)) {
    it(`${owner} open → the composer yields; closed → it is back`, () => {
      expect(chatInputFocused()).toBe(true);
      open();
      expect(chatInputFocused()).toBe(false);
      actions.closeAllOverlays();
      // ADR-104 D-3: the modal sweep leaves an approval pending; it closes
      // only when answered (or by Esc on the card itself).
      actions.closeToolApproval();
      actions.closeSplash();
      expect(chatInputFocused()).toBe(true);
    });
  }

  it("every …Open flag of the workspace is classified", () => {
    const flags = Object.keys(workspace).filter((k) => /Open$/.test(k));
    for (const flag of flags) expect(`${flag}: ${flag in OPEN_FLAGS}`).toBe(`${flag}: true`);
    for (const [flag, owner] of Object.entries(OPEN_FLAGS)) {
      if (owner.startsWith("--")) continue;
      setWorkspace(flag as never, true as never);
      expect(`${flag}: ${chatInputFocused()}`).toBe(`${flag}: false`);
      setWorkspace(flag as never, false as never);
    }
  });

  for (const [label, open] of [
    ["venue picker (/sessions new)", () => actions.openVenuePicker()],
    ["/connect wizard", () => actions.openConnectWizard("source")],
    ["remote source picker (/venue sources)", () => actions.openRemoteSourcePicker("start")],
  ] as const) {
    it(`Esc on the ${label} returns typing to the composer`, async () => {
      const setup = await mountApp();
      open();
      await settle(setup, 150);
      setup.mockInput.pressEscape();
      await settle(setup, 150);
      await setup.mockInput.typeText("hello");
      await settle(setup);
      expect(activeTab().chatDraft).toBe("hello");
      setup.renderer.destroy();
    });
  }
});

// ── (b) Esc closes one overlay: never the turn, its queue, the pane ────────

describe("Esc on a modal closes that modal only (b)", () => {
  for (const [label, open] of [
    ["help", () => actions.openHelp()],
    ["command palette", () => actions.openCommandPalette()],
    ["slash picker (/theme)", () => actions.openSlashPicker({ title: "theme", items: [] } as never)],
  ] as const) {
    it(`${label} over a streaming turn and a docked pane`, async () => {
      const calls: string[] = [];
      const setup = await mountApp();
      recordingBridge(calls);
      actions.openPane("settings");
      setWorkspace("chatStreaming", true);
      setWorkspace("pendingChatQueue", ["queued prompt"] as never);
      open();
      await settle(setup);
      setup.mockInput.pressEscape();
      await settle(setup);
      expect([workspace.helpOpen, workspace.commandPaletteOpen, workspace.slashPickerOpen]).toEqual([
        false,
        false,
        false,
      ]);
      expect(calls).toEqual([]);
      expect(workspace.chatStreaming).toBe(true);
      expect(workspace.pendingChatQueue).toHaveLength(1);
      expect(workspace.paneView).toBe("settings");
      setWorkspace("chatStreaming", false);
      setup.renderer.destroy();
    });
  }

  it("ask-user Esc declines the question, the turn keeps running", async () => {
    const calls: string[] = [];
    const setup = await mountApp();
    recordingBridge(calls);
    setWorkspace("chatStreaming", true);
    actions.openAskUser({ id: "q1", question: "pick one", options: ["a", "b"] });
    await settle(setup);
    setup.mockInput.pressEscape();
    await settle(setup);
    expect(calls).toEqual(["respondAskUser(null)"]);
    expect(workspace.askUser).toBeNull();
    expect(workspace.chatStreaming).toBe(true);
    setWorkspace("chatStreaming", false);
    setup.renderer.destroy();
  });

  it("closeAllOverlays (explicit close-everything) still interrupts and drops the pane", () => {
    const calls: string[] = [];
    recordingBridge(calls);
    actions.openPane("settings");
    setWorkspace("chatStreaming", true);
    actions.closeAllOverlays();
    expect(calls).toContain("cancelTurn");
    expect(workspace.paneView).toBeNull();
  });
});
