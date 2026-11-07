import { beforeEach, describe, expect, it } from "bun:test";
import { actions, workspace } from "../src/stores";
import { letterShortcutsBlocked } from "../src/lib/space-shortcuts";
import { tokenizeSlashArgs } from "../src/commands/slash";
import {
  buildCreateSlashLine,
  buildImportSlashLine,
  buildPlanSlashLine,
  mapProjectListToPickerItems,
  quoteSlashArg,
} from "../src/lib/start-research";

describe("start-research slash builders", () => {
  it("builds create with optional name", () => {
    expect(buildCreateSlashLine("demo09")).toBe("/project create demo09");
    expect(buildCreateSlashLine("demo09", "My Lab")).toBe(
      `/project create demo09 --name ${quoteSlashArg("My Lab")}`,
    );
  });

  it("builds import and round-trips quoted paths via tokenizeSlashArgs", () => {
    const win = String.raw`C:\Users\me\My Docs\paper.pdf`;
    const line = buildImportSlashLine("demo09", `${quoteSlashArg(win)} /tmp/a.csv`);
    expect(line.startsWith("/project import demo09 ")).toBe(true);
    const args = line.replace(/^\/project\s+/, "");
    const tokens = tokenizeSlashArgs(args);
    expect(tokens[0]).toBe("import");
    expect(tokens[1]).toBe("demo09");
    expect(tokens[2]).toBe(win);
    expect(tokens[3]).toBe("/tmp/a.csv");
  });

  it("builds plan with free-text goal", () => {
    expect(buildPlanSlashLine("demo09", "Investigate catalyst X")).toBe(
      "/project plan demo09 Investigate catalyst X",
    );
  });

  it("maps project_list to picker items", () => {
    const items = mapProjectListToPickerItems([
      { project_id: "a", name: "Alpha", active: true },
      { project_id: "b" },
    ]);
    expect(items).toEqual([
      { key: "a", label: "* a (Alpha)" },
      { key: "b", label: "  b" },
    ]);
  });
});

describe("startResearch gating", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    actions.setChatDraft("");
    actions.closeAllOverlays();
  });

  it("blocks letter shortcuts while startResearch overlay is open", () => {
    expect(letterShortcutsBlocked()).toBe(false);
    actions.openStartResearch("create");
    expect(workspace.startResearch?.kind).toBe("create");
    expect(letterShortcutsBlocked()).toBe(true);
    actions.closeStartResearch();
    expect(letterShortcutsBlocked()).toBe(false);
  });

  it("blocks while start actions menu is open", () => {
    actions.openStartActionsMenu();
    expect(workspace.startActionsMenuOpen).toBe(true);
    expect(letterShortcutsBlocked()).toBe(true);
    actions.closeStartActionsMenu();
    expect(letterShortcutsBlocked()).toBe(false);
  });

  it("closeAllOverlays clears startResearch", () => {
    actions.openStartResearch("plan");
    actions.openStartActionsMenu();
    actions.closeAllOverlays();
    expect(workspace.startResearch).toBeNull();
    expect(workspace.startActionsMenuOpen).toBe(false);
  });
});
