/**
 * Side-by-side diff transform tests. Pins: pairing of del/add runs, lone
 * adds, context duplication, hunk/meta full-width rows, no text invention
 * (every side row text is an input line minus prefix), width gate.
 */

import { describe, expect, test } from "bun:test";
import { preferSideBySide, toSideBySide } from "../src/lib/parallel-diff";

const SAMPLE = [
  "diff --git a/x.ts b/x.ts",
  "index 111..222 100644",
  "--- a/x.ts",
  "+++ b/x.ts",
  "@@ -1,4 +1,4 @@",
  " const a = 1;",
  "-const b = 2;",
  "+const b = 20;",
  "-const c = 3;",
  "+const c = 30;",
  "+const d = 40;",
  " const e = 5;",
  "-const gone = 9;",
  " const f = 6;",
].join("\n");

describe("toSideBySide", () => {
  const rows = toSideBySide(SAMPLE);

  test("hunks and headers render as full-width rows unchanged", () => {
    const fulls = rows.filter((r) => r.kind === "full").map((r) => r.text);
    expect(fulls).toContain("diff --git a/x.ts b/x.ts");
    expect(fulls).toContain("@@ -1,4 +1,4 @@");
    expect(fulls).toContain("--- a/x.ts");
  });

  test("del+add runs pair line-by-line (old left, new right)", () => {
    const pairs = rows.filter((r) => r.kind === "pair");
    const bRow = pairs.find((r) => r.left.text.includes("const b"));
    expect(bRow?.left.kind).toBe("del");
    expect(bRow?.left.text).toBe("const b = 2;");
    expect(bRow?.right.text).toBe("const b = 20;");
    expect(bRow?.right.kind).toBe("add");
  });

  test("extra addition lands on an empty left side", () => {
    const pairs = rows.filter((r) => r.kind === "pair");
    const dRow = pairs.find((r) => r.right.text.includes("const d"));
    expect(dRow?.left.kind).toBe("empty");
    expect(dRow?.right.kind).toBe("add");
  });

  test("lone deletion leaves right side empty", () => {
    const pairs = rows.filter((r) => r.kind === "pair");
    const gone = pairs.find((r) => r.left.text.includes("gone"));
    expect(gone?.left.kind).toBe("del");
    expect(gone?.right.kind).toBe("empty");
  });

  test("context lines appear on both sides without +/- prefix", () => {
    const pairs = rows.filter((r) => r.kind === "pair");
    const ctx = pairs.find((r) => r.left.text.includes("const a"));
    expect(ctx?.left.kind).toBe("ctx");
    expect(ctx?.left.text).toBe("const a = 1;");
    expect(ctx?.right.text).toBe("const a = 1;");
  });

  test("no invented text: every side row body traces to the input", () => {
    const inputBodies = new Set<string>();
    for (const l of SAMPLE.split("\n")) {
      inputBodies.add(l);
      if (l.startsWith("+") || l.startsWith("-") || l.startsWith(" ")) inputBodies.add(l.slice(1));
    }
    for (const row of rows) {
      if (row.kind === "full") {
        expect(inputBodies.has(row.text!)).toBe(true);
      } else {
        for (const side of [row.left, row.right]) {
          if (side.kind !== "empty") expect(inputBodies.has(side.text)).toBe(true);
        }
      }
    }
  });

  test("unified marker rows (---/+++) never leak into pair columns", () => {
    const pairTexts = rows.flatMap((r) => (r.kind === "pair" ? [r.left.text, r.right.text] : []));
    expect(pairTexts.some((t) => t.startsWith("--") || t.startsWith("++"))).toBe(false);
  });
});

describe("preferSideBySide", () => {
  test("narrow terminals stay unified", () => {
    expect(preferSideBySide(100)).toBe(false);
    expect(preferSideBySide(110)).toBe(true);
    expect(preferSideBySide(200)).toBe(true);
  });
});
