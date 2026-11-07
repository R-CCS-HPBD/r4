/**
 * Issue #217 (2026-10-27): Settings (F7) lists the existing preferences with
 * their values; activating a row toggles it or opens its owning surface.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, activeTab, workspace } from "../src/stores";
import {
  activatePreference,
  nextSettingsPanel,
  preferenceRowText,
  preferenceRows,
  type PreferenceSnapshot,
} from "../src/lib/settings-preferences";

const SNAP: PreferenceSnapshot = {
  bell: true,
  ambient: false,
  theme: "nord",
  model: null,
  trustSource: "",
  interactionMode: "chat",
  projectId: "proj-a",
  statusline: "",
};

const realDispatch = actions.dispatchSlashFromChat;

function recordDispatch(lines: string[]): void {
  (actions as Record<string, unknown>).dispatchSlashFromChat = async (line: string) => {
    lines.push(line);
    actions.clearChatDraft(); // what the real dispatch does first
    return { kind: "handled" as const };
  };
}

beforeEach(() => {
  actions.resetWorkspace();
});

afterEach(() => {
  (actions as Record<string, unknown>).dispatchSlashFromChat = realDispatch;
  actions.resetWorkspace();
});

describe("preferenceRows (pure)", () => {
  it("lists every preference with its value and where it lives", () => {
    const rows = preferenceRows(SNAP);
    expect(rows.map((r) => r.key)).toEqual(["bell", "ambient", "theme", "model", "mode", "rules", "statusline"]);
    const text = rows.map(preferenceRowText);
    expect(text).toContain("Completion bell: on (this session)");
    expect(text).toContain("Ambient event cards: off (this session)");
    expect(text).toContain("Theme: nord (saved)");
    expect(text).toContain("Model: auto (this session)");
    expect(text).toContain("Mode: chat (this session)");
    expect(text).toContain("Approval rules: proj-a (saved per project)");
    expect(text).toContain("Status line: off (saved)");
  });

  it("clips a long value; autopilot names its trust source", () => {
    const rows = preferenceRows({ ...SNAP, interactionMode: "autopilot", trustSource: "server", statusline: "echo a-very-long-status-line-command --flag" });
    const status = preferenceRowText(rows.find((r) => r.key === "statusline")!);
    expect(status).toBe("Status line: echo a-very-long-status… (saved)");
    expect(rows.find((r) => r.key === "mode")!.value).toBe("autopilot(policy)");
  });

  it("reads the live stores by default", () => {
    actions.setCompletionBellEnabled(false);
    expect(preferenceRows().find((r) => r.key === "bell")!.value).toBe("off");
    actions.setCompletionBellEnabled(true);
    expect(preferenceRows().find((r) => r.key === "bell")!.value).toBe("on");
  });
});

describe("activatePreference", () => {
  it("toggles the completion bell and ambient cards in place", async () => {
    expect(workspace.completionBellEnabled).toBe(true);
    await activatePreference("bell");
    expect(workspace.completionBellEnabled).toBe(false);
    await activatePreference("bell");
    expect(workspace.completionBellEnabled).toBe(true);
    await activatePreference("ambient");
    expect(workspace.ambientCardsEnabled).toBe(false);
  });

  it("opens the owning surface for every other row", async () => {
    const lines: string[] = [];
    recordDispatch(lines);
    for (const key of ["theme", "model", "mode", "rules", "statusline"] as const) {
      await activatePreference(key);
    }
    expect(lines).toEqual(["/theme", "/models", "/settings matrix", "/settings rules", "/statusline"]);
  });

  it("puts a half-typed draft back after opening a picker", async () => {
    const lines: string[] = [];
    recordDispatch(lines);
    actions.setChatDraft("half typed");
    await activatePreference("theme");
    expect(lines).toEqual(["/theme"]);
    expect(workspace.pendingChatDraft).toBe("half typed");
    expect(activeTab().chatDraft).toBe("");
  });
});

describe("nextSettingsPanel", () => {
  it("cycles Preferences → Skills → MCP → Preferences", () => {
    expect(nextSettingsPanel("prefs")).toBe("skills");
    expect(nextSettingsPanel("skills")).toBe("mcp");
    expect(nextSettingsPanel("mcp")).toBe("prefs");
  });
});
