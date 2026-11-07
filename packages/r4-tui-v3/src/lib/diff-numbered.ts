/**
 * Diff row numbering + per-side source reconstruction (opencode <diff>
 * parity groundwork): every body line of a unified diff maps to its old-
 * and/or new-file line number, and the del/ctx and add/ctx sequences
 * rebuild the old and new file texts — so the renderer can draw real
 * line-number gutters and tokenize each side ONCE with shiki instead of
 * coloring diff rows as flat text.
 */

import { classifyDiffLines, type DiffLineKind } from "./diff-highlight";

export interface NumberedDiffLine {
  kind: DiffLineKind;
  /** Line body WITHOUT the leading +/- sign or context space. */
  text: string;
  /** Old-file line number (1-based) — del/ctx only. */
  oldNo: number | null;
  /** New-file line number (1-based) — add/ctx only. */
  newNo: number | null;
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/**
 * Walk one unified diff, assigning per-file line numbers. Hunk/meta rows
 * carry no numbers. Malformed hunk headers keep numbering from the
 * previous position (diffs line-counted by tools we control are always
 * well-formed; foreign text stays honest).
 */
export function numberedDiffLines(diffText: string): NumberedDiffLine[] {
  const out: NumberedDiffLine[] = [];
  let oldCursor = 0;
  let newCursor = 0;
  for (const { kind, text } of classifyDiffLines(diffText)) {
    if (kind === "hunk") {
      const m = HUNK_RE.exec(text);
      if (m) {
        oldCursor = Number(m[1]);
        newCursor = Number(m[2]);
      }
      out.push({ kind, text, oldNo: null, newNo: null });
      continue;
    }
    if (kind === "meta") {
      out.push({ kind, text, oldNo: null, newNo: null });
      continue;
    }
    if (kind === "del") {
      out.push({ kind, text: text.slice(1), oldNo: oldCursor++, newNo: null });
      continue;
    }
    if (kind === "add") {
      out.push({ kind, text: text.slice(1), oldNo: null, newNo: newCursor++ });
      continue;
    }
    // ctx carries the leading context space in classifyDiffLines output.
    const body = text.startsWith(" ") ? text.slice(1) : text;
    out.push({ kind: "ctx", text: body, oldNo: oldCursor++, newNo: newCursor++ });
  }
  return out;
}

/** Old-file view (del + ctx bodies in order) for one-shot tokenization. */
export function oldSideLines(lines: NumberedDiffLine[]): string[] {
  return lines.filter((l) => l.kind === "del" || l.kind === "ctx").map((l) => l.text);
}

/** New-file view (add + ctx bodies in order) for one-shot tokenization. */
export function newSideLines(lines: NumberedDiffLine[]): string[] {
  return lines.filter((l) => l.kind === "add" || l.kind === "ctx").map((l) => l.text);
}

/**
 * For each add/del/ctx row: index of its body inside the matching side
 * array from oldSideLines/newSideLines ({side, index}); null rows for
 * hunk/meta (no token spans painted there).
 */
export function sideTokenIndex(
  lines: NumberedDiffLine[],
): ({ side: "old" | "new"; index: number } | null)[] {
  const out: ({ side: "old" | "new"; index: number } | null)[] = [];
  let oldIndex = 0;
  let newIndex = 0;
  for (const line of lines) {
    if (line.kind === "del") {
      out.push({ side: "old", index: oldIndex++ });
    } else if (line.kind === "add") {
      out.push({ side: "new", index: newIndex++ });
    } else if (line.kind === "ctx") {
      // Context exists on both sides; render uses the NEW side's spans.
      out.push({ side: "new", index: newIndex++ });
      oldIndex++;
    } else {
      out.push(null);
    }
  }
  return out;
}

/** Truncate with a visible ellipsis (the `__mai` clip class — cuts must
 * never look like intact content). */
export function truncateVis(text: string, budget: number): string {
  if (budget <= 0) return "";
  if (text.length <= budget) return text;
  return `${text.slice(0, Math.max(0, budget - 1))}…`;
}

/**
 * Attach per-side line numbers to side-by-side rows (walks rows in
 * encounter order — independent of del/add pairing, so pairRuns grouping
 * stays untouched). Full rows starting `@@` reset the counters.
 */
export function numberSideBySideRows<T extends { kind: string; left: { kind: string }; right: { kind: string }; text?: string }>(
  rows: T[],
): (T & { leftNo: number | null; rightNo: number | null })[] {
  let oldCursor = 0;
  let newCursor = 0;
  return rows.map((row) => {
    if (row.kind === "full") {
      const m = row.text ? HUNK_RE.exec(row.text) : null;
      if (m) {
        oldCursor = Number(m[1]);
        newCursor = Number(m[2]);
      }
      return { ...row, leftNo: null, rightNo: null };
    }
    let leftNo: number | null = null;
    let rightNo: number | null = null;
    if (row.left.kind === "del" || row.left.kind === "ctx") leftNo = oldCursor++;
    if (row.right.kind === "add" || row.right.kind === "ctx") rightNo = newCursor++;
    return { ...row, leftNo, rightNo };
  });
}
