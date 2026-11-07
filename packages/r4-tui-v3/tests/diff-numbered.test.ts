import { describe, expect, test } from "bun:test";
import {
  newSideLines,
  numberedDiffLines,
  numberSideBySideRows,
  oldSideLines,
  sideTokenIndex,
  truncateVis,
} from "../src/lib/diff-numbered";
import { toSideBySide } from "../src/lib/parallel-diff";

const DIFF = [
  "--- a/f.py",
  "+++ b/f.py",
  "@@ -1,4 +1,5 @@",
  " alpha",
  "-beta",
  "+BETA",
  " gamma",
  "+delta",
  " eps",
  "@@ -10,2 +11,3 @@",
  " one",
  "-two",
  "+TWO",
  "+three",
].join("\n");

describe("numberedDiffLines", () => {
  const lines = numberedDiffLines(DIFF);

  test("hunk headers reset and advance per-file counters", () => {
    expect(lines[2]).toMatchObject({ kind: "hunk", oldNo: null, newNo: null });
    expect(lines[3]).toMatchObject({ kind: "ctx", oldNo: 1, newNo: 1 });
    expect(lines[4]).toMatchObject({ kind: "del", oldNo: 2, newNo: null });
    expect(lines[5]).toMatchObject({ kind: "add", oldNo: null, newNo: 2 });
    expect(lines[6]).toMatchObject({ kind: "ctx", oldNo: 3, newNo: 3 });
    expect(lines[7]).toMatchObject({ kind: "add", oldNo: null, newNo: 4 });
    expect(lines[8]).toMatchObject({ kind: "ctx", oldNo: 4, newNo: 5 });
  });

  test("second hunk restarts at its own offsets", () => {
    expect(lines[10]).toMatchObject({ kind: "ctx", oldNo: 10, newNo: 11 });
    expect(lines[11]).toMatchObject({ kind: "del", oldNo: 11, newNo: null });
    expect(lines[12]).toMatchObject({ kind: "add", oldNo: null, newNo: 12 });
    expect(lines[13]).toMatchObject({ kind: "add", oldNo: null, newNo: 13 });
  });

  test("bodies carry no +/- prefix", () => {
    expect(lines[4]!.text).toBe("beta");
    expect(lines[5]!.text).toBe("BETA");
  });
});

describe("side reconstruction + token index", () => {
  const lines = numberedDiffLines(DIFF);

  test("old side = del+ctx in order; new side = add+ctx", () => {
    expect(oldSideLines(lines)).toEqual(["alpha", "beta", "gamma", "eps", "one", "two"]);
    expect(newSideLines(lines)).toEqual([
      "alpha", "BETA", "gamma", "delta", "eps", "one", "TWO", "three",
    ]);
  });

  test("index mapping points into the right side arrays", () => {
    const idx = sideTokenIndex(lines);
    expect(idx[4]).toEqual({ side: "old", index: 1 }); // "beta"
    expect(idx[5]).toEqual({ side: "new", index: 1 }); // "BETA"
    expect(idx[7]).toEqual({ side: "new", index: 3 }); // "delta"
    expect(idx[2]).toBeNull(); // hunk row
  });
});

describe("numberSideBySideRows", () => {
  test("pair rows carry per-side numbers, full rows carry none", () => {
    const rows = numberSideBySideRows(toSideBySide(DIFF));
    const pairs = rows.filter((r) => r.kind === "pair");
    // del/add pair: beta (old 2) | BETA (new 2)
    const change = pairs.find((r) => r.left.kind === "del")!;
    expect(change.leftNo).toBe(2);
    expect(change.rightNo).toBe(2);
    // ctx pair after the change: gamma (3|3)
    const ctx = pairs.find((r) => r.left.text === "gamma")!;
    expect(ctx.leftNo).toBe(3);
    expect(ctx.rightNo).toBe(3);
    const full = rows.filter((r) => r.kind === "full");
    expect(full.every((r) => r.leftNo === null && r.rightNo === null)).toBe(true);
  });
});

describe("truncateVis", () => {
  test("long text cuts with a visible marker, short text stays intact", () => {
    expect(truncateVis("abcdefghij", 5)).toBe("abcd…");
    expect(truncateVis("abc", 5)).toBe("abc");
    expect(truncateVis("abc", 0)).toBe("");
  });
});
