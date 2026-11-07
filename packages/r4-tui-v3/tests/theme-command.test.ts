/**
 * /theme command + theme registry contract: many themes, full token coverage,
 * alias resolution, singular mutable palette, epoch bump, persistence.
 */

import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { actions, resetWorkspace, workspace } from "../src/stores";
import {
  aurora,
  applyTheme,
  currentThemeName,
  listThemes,
  palette,
  resolveThemeName,
  themeEpoch,
} from "../src/themes";
import { parseThemePickerIntent, themePicker } from "../src/commands/slash-pickers";
import { dispatchSlashCommand, localSlashCommands } from "../src/commands/slash";

const tmpHome = mkdtempSync(join(tmpdir(), "r4-theme-test-"));
const env = { R4_CONFIG_DIR: tmpHome };
let savedConfigDir: string | undefined;

beforeAll(() => {
  // Redirect persistence away from the real ~/.config/r4 for this file.
  savedConfigDir = process.env.R4_CONFIG_DIR;
  process.env.R4_CONFIG_DIR = tmpHome;
});

afterAll(() => {
  if (savedConfigDir === undefined) delete process.env.R4_CONFIG_DIR;
  else process.env.R4_CONFIG_DIR = savedConfigDir;
  // Palette is a process-wide singleton — leave it on the default for others.
  applyTheme("aurora", { persist: false });
  rmSync(tmpHome, { recursive: true, force: true });
});

describe("theme registry", () => {
  it("offers at least 10 themes", () => {
    expect(listThemes().length).toBeGreaterThanOrEqual(10);
  });

  it("every theme carries the full aurora token set", () => {
    const keys = Object.keys(aurora);
    for (const theme of listThemes()) {
      const got = theme.colors as Record<string, string>;
      for (const key of keys) {
        expect(typeof got[key], `${theme.name}.${key}`).toBe("string");
        expect(got[key], `${theme.name}.${key}`).toMatch(/^#/);
      }
    }
  });

  it("resolves canonical names and aliases", () => {
    expect(resolveThemeName("aurora")).toBe("aurora");
    expect(resolveThemeName("tokyo")).toBe("tokyo-night");
    expect(resolveThemeName("Tokyo_Night")).toBe("tokyo-night");
    expect(resolveThemeName("gruvbox")).toBe("gruvbox-dark");
    expect(resolveThemeName("nope")).toBeNull();
  });
});

describe("applyTheme", () => {
  it("rewrites the palette singleton in place", () => {
    // Resilient start: another suite's theme application can leave the
    // singleton in dracula; idempotent replay must not cave.
    applyTheme("aurora", { persist: false });
    const before = palette.accent;
    const result = applyTheme("dracula", { persist: false });
    expect(result.ok).toBe(true);
    expect(currentThemeName()).toBe("dracula");
    expect(palette.accent).not.toBe(before);
    applyTheme("aurora", { persist: false });
  });

  it("bumps the remount epoch on every apply", () => {
    const e0 = themeEpoch();
    applyTheme("nord", { persist: false });
    expect(themeEpoch()).toBe(e0 + 1);
    applyTheme("aurora", { persist: false });
  });

  it("persists the choice to theme.json", () => {
    applyTheme("kanagawa", { env });
    const raw = JSON.parse(readFileSync(join(tmpHome, "theme.json"), "utf8")) as { theme?: string };
    expect(raw.theme).toBe("kanagawa");
    applyTheme("aurora", { persist: false });
  });

  it("rejects unknown names with the theme list in the error", () => {
    const result = applyTheme("bogus", { persist: false });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("aurora");
  });
});

describe("slash-bar/theme mirror", () => {
  it("THEME_SLASH_SUBCOMMANDS mirrors the theme registry (+ list)", () => {
    // Drift guard: the slash-table is a name-mirror of ../themes THEMES.
    const entry = localSlashCommands().find((e) => e.name === "theme");
    expect(entry?.subcommands).toEqual([...listThemes().map((t) => t.name), "list"]);
  });
});

describe("/theme command", () => {
  it("bare /theme is a picker intent; /theme <name> is not", () => {
    expect(parseThemePickerIntent("/theme")).toBe(true);
    expect(parseThemePickerIntent("/theme ")).toBe(true);
    expect(parseThemePickerIntent("/theme nord")).toBe(false);
    expect(parseThemePickerIntent("/mode")).toBe(false);
  });

  it("picker lists every theme and marks the current one", () => {
    applyTheme("aurora", { persist: false });
    const picker = themePicker();
    expect(picker.items.length).toBe(listThemes().length);
    expect(picker.commandPrefix).toBe("/theme ");
    const marked = picker.items.filter((i) => i.label.includes("← current"));
    expect(marked.length).toBe(1);
    expect(marked[0]!.key).toBe("aurora");
  });

  it("highlight previews live without persisting; Esc restores the opener theme", () => {
    const { existsSync, rmSync } = require("node:fs") as typeof import("node:fs");
    rmSync(join(tmpHome, "theme.json"), { force: true });
    applyTheme("nord", { persist: false });
    const picker = themePicker();

    const dracula = picker.items.find((i) => i.key === "dracula")!;
    picker.onHighlight?.(dracula);
    expect(currentThemeName()).toBe("dracula");
    // Preview must NOT write theme.json (persistence waits for Enter).
    expect(existsSync(join(tmpHome, "theme.json"))).toBe(false);
    // Highlight must NOT poke the store: the picker sits inside the epoch-keyed
    // remount (store-held selection), so a store-touch per highlight would
    // double-render. (Repaint correctness is pinned live in theme-focus-ui.)
    expect(workspace.slashPickerOpen).toBe(false);

    picker.onCancel?.();
    expect(currentThemeName()).toBe("nord");
    expect(existsSync(join(tmpHome, "theme.json"))).toBe(false);
    actions.closeSlashPicker();
    applyTheme("aurora", { persist: false });
  });

  it("/theme <name> applies the theme and notices the user", async () => {
    resetWorkspace();
    const result = await dispatchSlashCommand("/theme one-dark");
    expect(result.kind).toBe("handled");
    expect(currentThemeName()).toBe("one-dark");
    const notice = workspace_notices().at(-1) ?? "";
    expect(notice).toContain("theme → one-dark");
    applyTheme("aurora", { persist: false });
  });

  it("/theme list prints names inline", async () => {
    resetWorkspace();
    const result = await dispatchSlashCommand("/theme list");
    expect(result.kind).toBe("handled");
    expect(workspace_notices().at(-1) ?? "").toContain("aurora");
  });

  it("unknown theme returns an error listing valid names", async () => {
    resetWorkspace();
    const result = await dispatchSlashCommand("/theme bogus");
    expect(result.kind).toBe("error");
    if (result.kind === "error") expect(result.message).toContain("unknown theme");
  });
});

/** System-notice contents — notices land in chatMessages as system rows. */
function workspace_notices(): string[] {
  return workspace.chatMessages
    .filter((m) => m.role === "system")
    .map((m) => String(m.content ?? ""));
}

// Stabilization 2026-10-21 (slash-a#6, panes-overlays#10): the picker's
// ↑↓ preview applied the theme before Enter dispatched `/theme <name>`, so
// the receipt said "(already active)" for a theme that had just changed
// and persisted.
describe("/theme picker receipt", () => {
  it("names the theme active before the picker, not the preview", async () => {
    resetWorkspace();
    applyTheme("aurora", { persist: false });
    const picker = themePicker();
    picker.onHighlight?.({ key: "nord", label: "nord" });
    expect(currentThemeName()).toBe("nord");
    expect((await dispatchSlashCommand("/theme nord")).kind).toBe("handled");
    const last = workspace.chatMessages.at(-1)?.content ?? "";
    expect(last).toBe("theme → nord (was aurora) — persisted");
    expect(readFileSync(join(tmpHome, "theme.json"), "utf8")).toContain("nord");
  });

  it("a cancelled preview leaves later receipts alone", async () => {
    resetWorkspace();
    applyTheme("aurora", { persist: false });
    const picker = themePicker();
    picker.onHighlight?.({ key: "nord", label: "nord" });
    picker.onCancel?.();
    await dispatchSlashCommand("/theme aurora");
    expect(workspace.chatMessages.at(-1)?.content).toBe("theme → aurora (already active)");
  });
});
