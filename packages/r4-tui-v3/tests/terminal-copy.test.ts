/**
 * terminal/copy.ts — the pure wrap-aware core (2026-11-05).
 *
 * Stubs mimic IBufferLine: `isWrapped` true = the row CONTINUES the row
 * above; translateToString trims right and takes start/end columns
 * (end exclusive) — the same convention the real renderer's selection
 * follows (verified 2026-11-05: drag 2→6 over ASCII selects "BCCD").
 */

import { describe, expect, test } from "bun:test";
import {
  extractWrapAware,
  normalizeSpan,
  paneSelection,
  rowsToText,
  type CopyBufferLine,
  type CopyBufferSource,
} from "../src/terminal/copy";

function lineOf(text: string, wrapped = false): CopyBufferLine {
  return {
    isWrapped: wrapped,
    translateToString(trimRight?: boolean, start?: number, end?: number) {
      const raw = trimRight ? text.replace(/\s+$/, "") : text;
      return raw.slice(start ?? 0, end ?? raw.length);
    },
  };
}

function sourceOf(lines: CopyBufferLine[]): CopyBufferSource {
  return {
    length: lines.length,
    getLine: (y) => lines[y],
  };
}

const PANE = { x: 10, y: 4, cols: 40, rows: 12 };

describe("normalizeSpan", () => {
  test("a bottom-to-top drag normalizes with the x of each edge on its own row", () => {
    expect(normalizeSpan({ x: 30, y: 9 }, { x: 5, y: 2 })).toEqual({
      topY: 2,
      bottomY: 9,
      startX: 5,
      endX: 30,
    });
  });

  test("a right-to-left drag within one row normalizes", () => {
    expect(normalizeSpan({ x: 25, y: 7 }, { x: 8, y: 7 })).toEqual({
      topY: 7,
      bottomY: 7,
      startX: 8,
      endX: 25,
    });
  });
});

describe("paneSelection", () => {
  test("maps a fully-inside span into pane-relative rows and columns", () => {
    expect(paneSelection({ topY: 5, bottomY: 8, startX: 12, endX: 44 }, PANE)).toEqual({
      startRow: 1,
      endRow: 4,
      startCol: 2,
      endCol: 34,
    });
  });

  test("returns null when any row lies outside the rows area", () => {
    // Started ABOVE the pane (chat column drag into the terminal).
    expect(paneSelection({ topY: 3, bottomY: 6, startX: 12, endX: 20 }, PANE)).toBeNull();
    // Ended BELOW the rows (drag ending on the footer hint line).
    expect(paneSelection({ topY: 14, bottomY: 16, startX: 12, endX: 20 }, PANE)).toBeNull();
  });

  test("clamps columns into the row instead of leaking past it", () => {
    const sel = paneSelection({ topY: 4, bottomY: 4, startX: 0, endX: 999 }, PANE);
    expect(sel).toEqual({ startRow: 0, endRow: 0, startCol: 0, endCol: 40 });
  });
});

describe("extractWrapAware", () => {
  test("joins soft-wrapped rows WITHOUT a newline — the JWT case", () => {
    const token = "eyJ" + "a".repeat(97); // 100 chars, no spaces
    const rows = [lineOf(token.slice(0, 40)), lineOf(token.slice(40, 80), true), lineOf(token.slice(80), true)];
    const span = paneSelection({ topY: 4, bottomY: 6, startX: 10, endX: 30 }, PANE);
    expect(span).not.toBeNull();
    const text = extractWrapAware(sourceOf(rows), 0, span!);
    expect(text).toBe(token);
    expect(text).not.toContain("\n");
  });

  test("keeps hard newlines between unwrapped rows", () => {
    const rows = [lineOf("$ ls"), lineOf("a.txt  b.txt"), lineOf("$ pwd")];
    const span = paneSelection({ topY: 4, bottomY: 6, startX: 10, endX: 15 }, PANE);
    expect(extractWrapAware(sourceOf(rows), 0, span!)).toBe("$ ls\na.txt  b.txt\n$ pwd");
  });

  test("clips the first row from startCol and the last at endCol (exclusive)", () => {
    const rows = [lineOf("AABCCDDEEFFGGHH"), lineOf("0123456789")];
    const span = paneSelection({ topY: 4, bottomY: 5, startX: 12, endX: 16 }, PANE);
    // startCol 2 on row 0 → "BCCDDEEFFGGHH"; endCol 6 on row 1 → "012345".
    expect(extractWrapAware(sourceOf(rows), 0, span!)).toBe("BCCDDEEFFGGHH\n012345");
  });

  test("a drag that starts mid-wrap copies from the middle with no leading newline", () => {
    const rows = [lineOf("first-half-"), lineOf("second-half", true), lineOf("next cmd")];
    const span = paneSelection({ topY: 5, bottomY: 6, startX: 10, endX: 18 }, PANE);
    expect(extractWrapAware(sourceOf(rows), 0, span!)).toBe("second-half\nnext cmd");
  });

  test("honours the buffer view top (scrolled-back pane maps rows through topLine)", () => {
    const rows = [lineOf("old 0"), lineOf("old 1"), lineOf("new 0"), lineOf("new 1")];
    const span = paneSelection({ topY: 4, bottomY: 5, startX: 10, endX: 15 }, PANE);
    expect(extractWrapAware(sourceOf(rows), 2, span!)).toBe("new 0\nnew 1");
  });
});

describe("rowsToText (the /term share join)", () => {
  test("reproduces the plain per-row join for unwrapped rows (unchanged behaviour)", () => {
    expect(rowsToText([{ text: "one", wrapped: false }, { text: "two", wrapped: false }])).toBe("one\ntwo");
  });

  test("joins a wrapped tail into one logical line", () => {
    expect(
      rowsToText([
        { text: "$ cat token.txt", wrapped: false },
        { text: "eyJhbGciOi", wrapped: false },
        { text: "JIUzI1NiIs", wrapped: true },
        { text: "$ ", wrapped: false },
      ]),
    ).toBe("$ cat token.txt\neyJhbGciOiJIUzI1NiIs\n$ ");
  });

  test("an all-blank wrapped continuation row contributes nothing", () => {
    expect(
      rowsToText([{ text: "wraps-here", wrapped: false }, { text: "", wrapped: true }, { text: "next", wrapped: false }]),
    ).toBe("wraps-here\nnext");
  });
});
