// GFM pipe-table extraction (header row + |---| separator + body rows).
// Keeps parsing intentionally conservative: only contiguous GFM tables become
// table segments — everything else stays plain text, so nothing is silently
// reformatted that wasn't a table to begin with.

import { displayWidth, sliceColumns } from "./markdown-wrap";

export interface TableBlock {
  header: string[];
  rows: string[][];
}

export type ContentSegment =
  | { kind: "text"; lines: string[] }
  | { kind: "table"; table: TableBlock };

function isCandidateRow(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.length > 1 && trimmed.includes("|");
}

function isSeparatorRow(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return false;
  // Allow | --- | , |:--|--:|, ---|---
  return /^\|?[\s:-]+(\|[\s:-]*)+\|?$/.test(trimmed) && trimmed.includes("-");
}

function splitRow(line: string): string[] {
  let inner = line.trim();
  if (inner.startsWith("|")) inner = inner.slice(1);
  if (inner.endsWith("|")) inner = inner.slice(0, -1);
  return inner.split("|").map((cell) => cell.trim());
}

/** Split content lines into text/table segments, column-aware. */
export function splitTableSegments(lines: string[]): ContentSegment[] {
  const out: ContentSegment[] = [];
  let textRuns: string[] = [];
  let i = 0;
  const flush = () => {
    if (textRuns.length > 0) {
      out.push({ kind: "text", lines: textRuns });
      textRuns = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i]!;
    // Header? Candidate row followed by a separator row.
    if (
      isCandidateRow(line) &&
      i + 1 < lines.length &&
      isSeparatorRow(lines[i + 1]!)
    ) {
      flush();
      const header = splitRow(line);
      const rows: string[][] = [];
      let j = i + 2;
      while (j < lines.length && isCandidateRow(lines[j]!) && !isSeparatorRow(lines[j]!)) {
        rows.push(splitRow(lines[j]!));
        j++;
      }
      out.push({ kind: "table", table: { header, rows } });
      i = j;
      continue;
    }
    textRuns.push(line);
    i++;
  }
  flush();
  return out;
}

/** Grid chrome for `cols` columns: "│ " + (cols-1) × " │ " + " │". */
function gridChrome(cols: number): number {
  return 3 * cols + 1;
}

/**
 * Narrow the widest column one step at a time until the grid (cells +
 * chrome) fits `total` columns, never below `minCol`. Stabilization
 * 2026-10-21 (resize-render#7): the grid used to ignore the chat column,
 * so whole right-hand columns and the right border vanished past the edge
 * with no marker; now every column stays visible and long cells crop with
 * the `…` that fitCell already paints.
 */
export function shrinkColumnsToFit(widths: number[], total: number, minCol = 3): number[] {
  const out = [...widths];
  let over = out.reduce((a, b) => a + b, 0) + gridChrome(out.length) - total;
  while (over > 0) {
    const widest = out.indexOf(Math.max(...out));
    if (out[widest]! <= minCol) break;
    out[widest]! -= 1;
    over -= 1;
  }
  return out;
}

/** Column widths (display columns, CJK = 2) capped per-cell for sane
 * narrow-terminal rendering, then shrunk to fit `maxTotal` when given. */
export function tableColumnWidths(
  table: TableBlock,
  maxPerColumn = 28,
  maxTotal = Number.POSITIVE_INFINITY,
): number[] {
  const colCount = Math.max(
    table.header.length,
    ...table.rows.map((r) => r.length),
    1,
  );
  const widths = new Array<number>(colCount).fill(0);
  for (const row of [table.header, ...table.rows]) {
    row.forEach((cell, index) => {
      widths[index] = Math.max(widths[index]!, displayWidth(cell));
    });
  }
  const capped = widths.map((w) => Math.max(1, Math.min(w, maxPerColumn)));
  return Number.isFinite(maxTotal) ? shrinkColumnsToFit(capped, maxTotal) : capped;
}

/** Mono-spaced cell — pads with spaces, crops with the single-run ellipsis
 * (display columns: a CJK cell pads/crops by its painted width). */
export function fitCell(text: string, width: number): string {
  const w = displayWidth(text);
  if (w <= width) return text + " ".repeat(width - w);
  const head = sliceColumns(text, Math.max(1, width - 1));
  return `${head}…${" ".repeat(Math.max(0, width - 1 - displayWidth(head)))}`;
}
