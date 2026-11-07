import { describe, expect, test } from "bun:test";
import {
  RESEARCH_TAB_ALTGR_KEYS,
  RESEARCH_TAB_FN_KEYS,
  RESEARCH_TAB_FALLBACK_KEYS,
  SPACE_FALLBACK_KEYS,
  SPACE_FN_KEYS,
  SPACE_SHORTCUTS,
} from "../src/keymap";
import { ACTIVITY_TAB_DIGIT_KEYS, ACTIVITY_TAB_CYCLE_KEYS, ACTIVITY_TABS, ACTIVITY_TOGGLE_KEYS } from "../src/lib/activity-tabs";
import { getCommands } from "../src/commands";
import { actions, workspace } from "../src/stores";
import { letterShortcutsBlocked } from "../src/lib/space-shortcuts";

describe("keymap WSL/Windows fallbacks", () => {
  test("F-keys map to seven spaces including loop", () => {
    expect(SPACE_FN_KEYS).toEqual(["f1", "f2", "f3", "f4", "f5", "f6", "f7"]);
    expect(SPACE_SHORTCUTS).toEqual([
      "dashboard",
      "research",
      "tasks",
      "knowledge",
      "infrastructure",
      "loop",
      "settings",
    ]);
    expect(SPACE_SHORTCUTS[5]).toBe("loop");
    expect(SPACE_SHORTCUTS[6]).toBe("settings");
  });

  test("space shift fallbacks mirror ctrl+1-7 order", () => {
    expect(SPACE_FALLBACK_KEYS).toEqual([
      "ctrl+shift+1",
      "ctrl+shift+2",
      "ctrl+shift+3",
      "ctrl+shift+4",
      "ctrl+shift+5",
      "ctrl+shift+6",
      "ctrl+shift+7",
    ]);
  });

  test("research tab fallbacks are modifier-only — comma chords retired (prefix swallowed typing)", () => {
    expect(RESEARCH_TAB_ALTGR_KEYS).toEqual([
      "ctrl+alt+shift+1",
      "ctrl+alt+shift+2",
      "ctrl+alt+shift+3",
    ]);
    expect(RESEARCH_TAB_FN_KEYS).toEqual([]);
    expect(RESEARCH_TAB_FALLBACK_KEYS).toEqual(["alt+1", "alt+2", "alt+3"]);
    // retired family must not creep back
    expect(RESEARCH_TAB_FALLBACK_KEYS.some((k) => k.includes(","))).toBe(false);
  });

  test("activity tray uses digits when open + page cycle (no letter chords)", () => {
    expect(ACTIVITY_TABS).toHaveLength(6);
    expect(ACTIVITY_TAB_CYCLE_KEYS.prev).toEqual(["ctrl+pageup", "ctrl+shift+["]);
    expect(ACTIVITY_TAB_CYCLE_KEYS.next).toEqual(["ctrl+pagedown", "ctrl+shift+]"]);
    expect(ACTIVITY_TAB_DIGIT_KEYS).toEqual(["1", "2", "3", "4", "5", "6"]);
  });

  test("activity toggle avoids host-stolen chords (WT select-all, Cursor terminal)", () => {
    expect(ACTIVITY_TOGGLE_KEYS).toEqual(["f9", "ctrl+shift+y"]);
    expect([...ACTIVITY_TOGGLE_KEYS]).not.toContain("ctrl+shift+a");
    expect([...ACTIVITY_TOGGLE_KEYS]).not.toContain("ctrl+`");
  });

  test("letterShortcutsBlocked while verify focused (a must not open activity)", () => {
    actions.resetWorkspace();
    expect(letterShortcutsBlocked()).toBe(false);
    actions.setVerifyInputFocused(true);
    expect(letterShortcutsBlocked()).toBe(true);
    actions.setVerifyInputFocused(false);
  });

  test("knowledge pane Tab focuses verify and blocks letter shortcuts", () => {
    actions.resetWorkspace();
    actions.setSpace("knowledge");
    expect(workspace.knowledgePane).toBe("catalog");
    actions.setKnowledgePane("verify");
    expect(workspace.knowledgePane).toBe("verify");
    expect(workspace.verifyInputFocused).toBe(true);
    expect(letterShortcutsBlocked()).toBe(true);
    actions.setKnowledgePane("catalog");
    expect(workspace.knowledgePane).toBe("catalog");
    expect(workspace.verifyInputFocused).toBe(false);
  });

  test("command palette lists the pruned S2 command set", () => {
    const commands = getCommands();
    // S2 prune: no F-key shortcuts defined as defaults (power users re-add them
    // via ~/.config/r4/v3-keymap.json); the palette IS the canonical action path.
    expect(commands.find((c) => c.id === "ui:goto-research")).toBeDefined();
    expect(commands.find((c) => c.id === "ui:goto-research")?.shortcut).toBeUndefined();
    expect(commands.find((c) => c.id === "ui:goto-workflows")).toBeDefined();
    expect(commands.find((c) => c.id === "ui:help")).toBeDefined();
    expect(commands.find((c) => c.id === "ui:slash-bar")?.shortcut).toContain("ctrl+.");
    // Removed in S2: activity/context toggles and help overlay keys.
    expect(commands.find((c) => c.id === "ui:toggle-help")).toBeUndefined();
    expect(commands.find((c) => c.id === "ui:toggle-context-panel")).toBeUndefined();
    expect(commands.find((c) => c.id === "ui:toggle-activity-console")).toBeUndefined();
  });

  test("letterShortcutsBlocked while startResearch overlay open", () => {
    actions.resetWorkspace();
    actions.closeAllOverlays();
    expect(letterShortcutsBlocked()).toBe(false);
    actions.openStartResearch("import");
    expect(letterShortcutsBlocked()).toBe(true);
    actions.closeStartResearch();
  });
});
