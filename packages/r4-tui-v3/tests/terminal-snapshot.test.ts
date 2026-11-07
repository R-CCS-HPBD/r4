/**
 * Terminal pane: xterm buffer → styled rows.
 */

import { describe, expect, test } from "bun:test";
import { Terminal } from "@xterm/headless";
import { snapshotRows, type BufferSnapshotSource, type SnapshotOptions } from "../src/terminal/snapshot";

function makeTerminal(cols = 20, rows = 4): Terminal {
  return new Terminal({ cols, rows, scrollback: 20, allowProposedApi: true });
}

/** xterm defers parsing to the write callback — await it in tests. */
function writeAll(term: Terminal, data: string): Promise<void> {
  return new Promise((resolve) => term.write(data, () => resolve()));
}

function asBuf(term: Terminal): BufferSnapshotSource {
  return term.buffer.active as unknown as BufferSnapshotSource;
}

function rowText(row: { chunks: Array<{ text: string }> }): string {
  return row.chunks.map((c) => c.text).join("");
}

const DEFAULT_FG = "#cccccc";
const BLOCK_FG = "#202020";
const HOLLOW_BG = "#303030";

function opts(partial: Partial<SnapshotOptions> = {}): SnapshotOptions {
  return {
    cols: 20, rows: 4, top: 0,
    defaultFg: DEFAULT_FG, blockFg: BLOCK_FG, hollowBg: HOLLOW_BG,
    ...partial,
  };
}

describe("terminal snapshot", () => {
  test("plain text lands in one chunk with the default fg", async () => {
    const term = makeTerminal();
    await writeAll(term, "hello world");
    const rows = snapshotRows(asBuf(term), opts());
    expect(rows.length).toBe(4);
    expect(rowText(rows[0]!).startsWith("hello world")).toBe(true);
    expect(rows[0]!.chunks[0]!.text).toContain("hello");
  });

  test("colored output keeps per-cell colors", async () => {
    const term = makeTerminal();
    await writeAll(term, "\x1b[31mRED\x1b[0m plain");
    const rows = snapshotRows(asBuf(term), opts());
    expect(rows[0]!.chunks.map((c) => c.text).join("")).toContain("RED plain");
    const redChunk = rows[0]!.chunks.find((c) => c.text.includes("RED"))!;
    const plainChunk = rows[0]!.chunks.find((c) => c.text.includes("plain"))!;
    expect(redChunk).not.toBe(plainChunk);
  });

  test("cursor cell: block (focused) swaps to the bg-as-block color", async () => {
    const term = makeTerminal();
    await writeAll(term, "ab");
    const buf = asBuf(term);
    const withCursor = snapshotRows(buf, opts({
      cursor: { x: 2, y: 0, style: "block" },
    }));
    const without = snapshotRows(buf, opts({ cursor: null }));
    expect(JSON.stringify(withCursor[0]!.chunks)).not.toBe(JSON.stringify(without[0]!.chunks));
    const cursorChunk = withCursor[0]!.chunks.find((c) => c.text.trim() === "" || c.text === " ")!;
    expect(cursorChunk).toBeTruthy();
  });

  test("cursor cell: hollow (unfocused) keeps the glyphs, tone behind", async () => {
    const term = makeTerminal();
    await writeAll(term, "ab");
    const rows = snapshotRows(asBuf(term), opts({
      cursor: { x: 0, y: 0, style: "hollow" },
    }));
    // The first cell keeps its letter —
    expect(rowText(rows[0]!).startsWith("ab")).toBe(true);
    // but the chunk carrying it differs from the plain row.
    const plain = snapshotRows(asBuf(term), opts({ cursor: null }));
    expect(JSON.stringify(rows[0]!.chunks)).not.toBe(JSON.stringify(plain[0]!.chunks));
  });

  test("rows beyond the buffer render blank", async () => {
    const term = makeTerminal(20, 2);
    await writeAll(term, "x");
    const rows = snapshotRows(asBuf(term), opts());
    expect(rows.length).toBe(4);
    expect(rowText(rows[3]!).trim()).toBe("");
  });

  test("top offset reads scrollback", async () => {
    const term = makeTerminal(8, 2);
    // Overflow the viewport so older lines move into scrollback.
    await writeAll(term, "l1\r\nl2\r\nl3\r\nl4\r\n");
    const buf = asBuf(term);
    const scrolled = snapshotRows(buf, opts({
      cols: 8, rows: 2, top: Math.max(0, term.buffer.active.baseY - 2),
    }));
    expect(rowText(scrolled[0]!)).toContain("l2");
  });

  test("tab stops keep their gap: ls columns stay apart", async () => {
    const term = makeTerminal(40);
    await writeAll(term, "AGENTS.md\tROADMAP.md\tdocs");
    const rows = snapshotRows(asBuf(term), opts({ cols: 40 }));
    expect(rowText(rows[0]!)).toBe("AGENTS.md       ROADMAP.md      docs");
  });

  test("a cursor jump (CSI n C) leaves spaces, not glued text", async () => {
    const term = makeTerminal(30);
    await writeAll(term, "left\x1b[6Cright");
    const rows = snapshotRows(asBuf(term), opts({ cols: 30 }));
    expect(rowText(rows[0]!)).toBe("left      right");
  });

  test("cells past the last character stay unpainted", async () => {
    const term = makeTerminal();
    await writeAll(term, "a\tb");
    const rows = snapshotRows(asBuf(term), opts());
    expect(rowText(rows[0]!)).toBe("a       b");
    expect(rowText(rows[1]!)).toBe("");
  });

  test("the cursor after a tab still paints its cell", async () => {
    const term = makeTerminal();
    await writeAll(term, "ab\t");
    const rows = snapshotRows(asBuf(term), opts({ cursor: { x: 8, y: 0, style: "block" } }));
    expect(rowText(rows[0]!)).toBe("ab       ");
  });
});
