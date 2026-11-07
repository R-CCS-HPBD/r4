/**
 * Unified-diff → side-by-side pairing tests.
 */

import { describe, expect, it } from "bun:test";
import { parseUnifiedToRows, capRows } from "../src/lib/diff-view";

const SAMPLE = [
  "--- a/app.py",
  "+++ b/app.py",
  "@@ -1,4 +1,4 @@",
  " import os",
  "-OLD_VALUE = 1",
  "+NEW_VALUE = 2",
  " ",
  " def main():",
  "@@ -20,3 +20,4 @@",
  "-a",
  "-b",
  "+c",
  " keep",
].join("\n");

describe("parseUnifiedToRows", () => {
  it("pairs a single-line edit as one mod row", () => {
    const rows = parseUnifiedToRows(SAMPLE);
    const mod = rows.find((r) => r.left.kind === "mod" || r.right.kind === "mod");
    expect(mod).toBeDefined();
    expect(mod!.left.text).toBe("OLD_VALUE = 1");
    expect(mod!.right.text).toBe("NEW_VALUE = 2");
    expect(mod!.left.no).toBe(2);
    expect(mod!.right.no).toBe(2);
  });

  it("context lines appear on both columns with numbers", () => {
    const rows = parseUnifiedToRows(SAMPLE);
    const ctx = rows.find((r) => r.left.kind === "ctx" && r.left.text === "import os");
    expect(ctx?.left.no).toBe(1);
    expect(ctx?.right.no).toBe(1);
  });

  it("two deletions + one addition: one mod row + one del-only row", () => {
    const rows = parseUnifiedToRows(SAMPLE);
    const hunk2 = rows.filter((r) => ["mod", "del", "add"].includes(r.left.kind) ||
                                     ["mod", "del", "add"].includes(r.right.kind));
    const delOnly = hunk2.filter((r) => r.right.kind === "empty");
    expect(delOnly.length).toBeGreaterThanOrEqual(1);
  });

  it("headers are skipped", () => {
    const rows = parseUnifiedToRows(SAMPLE);
    expect(rows.some((r) => r.left.text.startsWith("---"))).toBe(false);
  });
});

describe("capRows", () => {
  it("caps rows with a marker", () => {
    const rows = parseUnifiedToRows(SAMPLE);
    const capped = capRows(rows, 1);
    expect(capped.length).toBe(2);
    expect(capped[1]!.left.text).toContain("more diff rows");
  });
});
