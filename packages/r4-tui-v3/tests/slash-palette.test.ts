import { describe, expect, it } from "bun:test";
import { buildSlashPaletteCommands, paletteQueryMode } from "../src/lib/slash-palette";
import type { SlashCommandEntry } from "../src/commands/slash";

const entries: SlashCommandEntry[] = [
  { name: "health", help: "Gateway health", category: "endpoint", takesArgs: false },
  { name: "project", help: "Project lifecycle", category: "project", takesArgs: true },
  { name: "quit", help: "Quit", category: "ui", takesArgs: false, hidden: true },
];

describe("D2 slash → palette fold", () => {
  it("maps visible verbs to namespaced palette commands", () => {
    const cmds = buildSlashPaletteCommands(entries, { dispatch: () => {}, insertDraft: () => {} });
    expect(cmds.map((c) => c.id)).toEqual(["slash:health", "slash:project"]);
    expect(cmds[0]?.namespace).toBe("slash/endpoint");
    expect(cmds[1]?.title).toContain("/project …");
  });

  it("hides hidden verbs unless includeHidden is set", () => {
    const visible = buildSlashPaletteCommands(entries, { dispatch: () => {}, insertDraft: () => {} });
    expect(visible.find((c) => c.id === "slash:quit")).toBeUndefined();
    const all = buildSlashPaletteCommands(
      entries,
      { dispatch: () => {}, insertDraft: () => {} },
      { includeHidden: true },
    );
    expect(all.find((c) => c.id === "slash:quit")).toBeDefined();
  });

  it("no-arg verbs dispatch their slash line", () => {
    const dispatched: string[] = [];
    const cmds = buildSlashPaletteCommands(entries, { dispatch: (l) => { dispatched.push(l); }, insertDraft: () => {} });
    cmds.find((c) => c.id === "slash:health")?.run();
    expect(dispatched).toEqual(["/health"]);
  });

  it("takesArgs verbs seed the composer draft instead of dispatching", () => {
    const drafts: string[] = [];
    const dispatched: string[] = [];
    const cmds = buildSlashPaletteCommands(entries, {
      dispatch: (l) => { dispatched.push(l); },
      insertDraft: (t) => { drafts.push(t); },
    });
    cmds.find((c) => c.id === "slash:project")?.run();
    expect(drafts).toEqual(["/project "]);
    expect(dispatched).toEqual([]);
  });

  it("takesArgs verbs WITH subcommands dispatch bare (picker parents — one Enter)", () => {
    const dispatched: string[] = [];
    const drafts: string[] = [];
    const pickerParent: SlashCommandEntry = {
      name: "theme",
      help: "Color theme",
      category: "ui",
      takesArgs: true,
      subcommands: ["aurora", "nord"],
    };
    const cmds = buildSlashPaletteCommands([pickerParent], {
      dispatch: (l) => { dispatched.push(l); },
      insertDraft: (t) => { drafts.push(t); },
    });
    cmds.find((c) => c.id === "slash:theme")?.run();
    expect(dispatched).toEqual(["/theme"]);
    expect(drafts).toEqual([]);
  });

  it("query mode: leading ! reveals hidden and strips from search", () => {
    expect(paletteQueryMode("!qui")).toEqual({ includeHidden: true, search: "qui" });
    expect(paletteQueryMode("  proj")).toEqual({ includeHidden: false, search: "proj" });
    expect(paletteQueryMode("!")).toEqual({ includeHidden: true, search: "" });
  });
});
