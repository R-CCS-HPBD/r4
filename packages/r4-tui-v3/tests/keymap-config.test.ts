import { describe, expect, it } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { keymapConfigPath, loadKeymapOverrides, mergeKeymapOverrides } from "../src/lib/keymap-config";
import { groupBindings, whichKeyRows } from "../src/lib/which-key";

describe("D1 keymap config", () => {
  it("resolves under R4_CONFIG_HOME when set, else ~/.config/r4", () => {
    expect(keymapConfigPath("/home/u")).toMatch(/v3-keymap\.json$/);
  });

  it("returns {} when the file is missing", () => {
    expect(loadKeymapOverrides("/does/not/exist.json")).toEqual({});
  });

  it("parses valid overrides and drops junk entries", () => {
    const dir = mkdtempSync(join(tmpdir(), "r4-keymap-"));
    const file = join(dir, "v3-keymap.json");
    writeFileSync(
      file,
      JSON.stringify({ "palette.open": "ctrl+alt+p", junkNumber: 42, "": "x", noTrim: "  " }),
    );
    const overrides = loadKeymapOverrides(file);
    expect(overrides).toEqual({ "palette.open": "ctrl+alt+p", "": "x" });
    rmSync(dir, { recursive: true, force: true });
  });

  it("merge is additive, deduped and drops unknown commands", () => {
    const base = [{ key: "ctrl+p", cmd: "palette.open" }];
    const merged = mergeKeymapOverrides(
      base,
      { "palette.open": "ctrl+alt+p", "bogus.cmd": "f2", "palette.open2": "x" },
      new Set(["palette.open"]),
    );
    expect(merged).toHaveLength(2);
    expect(merged[1]).toEqual({ key: "ctrl+alt+p", cmd: "palette.open" });
    // Re-applying the same key+cmd as the base set adds nothing.
    const idem = mergeKeymapOverrides(base, { "palette.open": "ctrl+p" }, new Set(["palette.open"]));
    expect(idem).toHaveLength(1);
  });

  it("merge keeps default binding even when override remaps the same cmd", () => {
    const base = [{ key: "ctrl+p", cmd: "palette.open" }];
    const merged = mergeKeymapOverrides(base, { "palette.open": "ctrl+alt+p" }, new Set(["palette.open"]));
    expect(merged[0]).toEqual(base[0]);
  });
});

describe("D1 which-key grouping", () => {
  it("groups by cmd namespace with space tags", () => {
    const groups = groupBindings([
      { key: "ctrl+p", cmd: "palette.open" },
      { key: "r", cmd: "research.runDemo", space: "research" },
      { key: "c", cmd: "research.cancelJob", space: "research" },
      { key: "escape", cmd: "overlay.close" },
    ]);
    expect(groups.map((g) => g.group)).toEqual(["overlay", "palette", "research@research"]);
  });

  it("rows cap entries per group with an overflow marker", () => {
    const groups = groupBindings(
      Array.from({ length: 7 }, (_, i) => ({ key: `k${i}`, cmd: `x.c${i}` })),
    );
    const rows = whichKeyRows(groups, 4);
    expect(rows[0]).toBe("◇ x");
    expect(rows.filter((r) => r.includes("more"))).toEqual(["  … +3 more"]);
  });
});
