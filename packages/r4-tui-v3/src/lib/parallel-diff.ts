/**
 * Parallel (side-by-side) diff layout.
 *
 * Pure transform unified-diff → paired columns. Within a hunk a deletion
 * run followed by an addition run pairs line k on the left with line k on
 * the right; shorter side gets "". Context lines appear on both columns.
 * Everything else (@@ hunks, ---/+++ headers, `diff`/`index` metas) renders
 * as a full-width row. No text is invented: every emitted line is a line of
 * the input minus its +/- prefix.
 */

export type DiffSideKind = "add" | "del" | "ctx" | "empty";

export interface DiffSide {
  text: string;
  kind: DiffSideKind;
}

export interface SideBySideRow {
  kind: "pair" | "full";
  left: DiffSide;
  right: DiffSide;
  /** full-width text when kind === "full" (hunk/meta). */
  text?: string;
}

const EMPTY_SIDE: DiffSide = { text: "", kind: "empty" };

const META_PREFIXES = [
  "@@",
  "diff ",
  "index ",
  "Binary ",
  "new file",
  "deleted file",
  "similarity ",
  "rename ",
];

function isFullWidth(line: string): boolean {
  if (line.startsWith("--- ") || line.startsWith("+++ ")) return true;
  if (line.startsWith("\\")) return true; // "\ No newline at end of file"
  return META_PREFIXES.some((prefix) => line.startsWith(prefix));
}

function isDel(line: string): boolean {
  return line.startsWith("-") && !line.startsWith("---");
}

function isAdd(line: string): boolean {
  return line.startsWith("+") && !line.startsWith("+++");
}

/** Collect one run of lines matching `keep`, stripping the prefix char. */
function collectRun(lines: string[], start: number, keep: (line: string) => boolean): {
  bodies: string[];
  next: number;
} {
  const bodies: string[] = [];
  let i = start;
  while (i < lines.length && keep(lines[i]!)) {
    bodies.push(lines[i]!.slice(1));
    i += 1;
  }
  return { bodies, next: i };
}

/** Zip a (possibly empty) del run with a (possibly empty) add run. */
function pairRuns(dels: string[], adds: string[]): SideBySideRow[] {
  const span = Math.max(dels.length, adds.length);
  const rows: SideBySideRow[] = [];
  for (let k = 0; k < span; k += 1) {
    rows.push({
      kind: "pair",
      left: k < dels.length ? { text: dels[k]!, kind: "del" } : EMPTY_SIDE,
      right: k < adds.length ? { text: adds[k]!, kind: "add" } : EMPTY_SIDE,
    });
  }
  return rows;
}

/** Emit the change starting at `i`: del run (+optional add run) or lone add run. */
function changeRows(lines: string[], start: number): { rows: SideBySideRow[]; next: number } {
  const dels = collectRun(lines, start, isDel);
  const adds = collectRun(lines, dels.next, isAdd);
  return { rows: pairRuns(dels.bodies, adds.bodies), next: adds.next };
}

function fullRow(text: string): SideBySideRow {
  return { kind: "full", left: EMPTY_SIDE, right: EMPTY_SIDE, text };
}

function ctxRow(line: string): SideBySideRow {
  const ctx = line.startsWith(" ") ? line.slice(1) : line;
  return {
    kind: "pair",
    left: { text: ctx, kind: "ctx" },
    right: { text: ctx, kind: "ctx" },
  };
}

export function toSideBySide(diffText: string): SideBySideRow[] {
  const rows: SideBySideRow[] = [];
  const lines = String(diffText).split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (isFullWidth(line)) {
      rows.push(fullRow(line));
      i += 1;
    } else if (isDel(line) || isAdd(line)) {
      const change = changeRows(lines, i);
      rows.push(...change.rows);
      i = change.next;
    } else {
      rows.push(ctxRow(line));
      i += 1;
    }
  }
  return rows;
}

/** True when a side-by-side render adds value at this width. */
export function preferSideBySide(cols: number): boolean {
  return cols >= 110;
}
