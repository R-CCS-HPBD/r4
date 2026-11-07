/**
 * Pane/chat focus model (user report 2026-10-06): a docked pane must NOT
 * disable the composer. Chat keeps the keys until the user explicitly
 * moves focus into the pane (Tab / click); pane-scoped verbs fire only
 * while the pane owns the keys (whenPaneFocused), so letters typed into
 * chat can never collide with pane shortcuts.
 */

import { beforeEach, describe, expect, test } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { chatInputFocused } from "../src/lib/chat-input-focus";
import { paneTitle } from "../src/lib/pane-title";

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("composer availability with a docked pane", () => {
  test("pane open (unfocused): composer keeps focus", () => {
    expect(chatInputFocused()).toBe(true);
    actions.openPane("sessions");
    expect(workspace.paneView).toBe("sessions");
    expect(workspace.paneFocus).toBe(false);
    expect(chatInputFocused()).toBe(true);
  });

  test("focus INTO the pane blurs the composer; toggling out restores it", () => {
    actions.openPane("sessions");
    actions.togglePaneFocus();
    expect(workspace.paneFocus).toBe(true);
    expect(chatInputFocused()).toBe(false);
    actions.togglePaneFocus();
    expect(workspace.paneFocus).toBe(false);
    expect(chatInputFocused()).toBe(true);
  });

  test("terminal view keeps its click-only contract", () => {
    actions.openPane("term");
    expect(chatInputFocused()).toBe(true);
    actions.togglePaneFocus(); // refused by the store (no key moves term focus)
    expect(workspace.paneFocus).toBe(false);
    expect(chatInputFocused()).toBe(true);
    // …but once CLICKED into focus the composer yields, as before.
    setWorkspace("paneFocus", true);
    expect(chatInputFocused()).toBe(false);
  });

  test("closing a focused pane returns the composer", () => {
    actions.openPane("codeview");
    actions.togglePaneFocus();
    expect(chatInputFocused()).toBe(false);
    actions.closePane();
    expect(workspace.paneView).toBe(null);
    expect(workspace.paneFocus).toBe(false);
    expect(chatInputFocused()).toBe(true);
  });
});

describe("pane title affordance (honest hints)", () => {
  test("focused pane: bare title", () => {
    expect(paneTitle("sessions", true)).toBe("Sessions");
  });
  test("unfocused, Tab-free views advertise Tab", () => {
    expect(paneTitle("sessions", false)).toBe("Sessions · Tab to focus");
    expect(paneTitle("codeview", false)).toBe("Code view · Tab to focus");
    expect(paneTitle("work", false)).toBe("Work · Tab to focus");
  });
  test("Tab-consuming views advertise click instead (research/knowledge own Tab)", () => {
    expect(paneTitle("research", false)).toBe("Research · click to focus");
    expect(paneTitle("knowledge", false)).toBe("Knowledge · click to focus");
  });
  test("terminal keeps its click-only hint", () => {
    expect(paneTitle("term", false)).toBe("Terminal · click to focus");
  });
});
