/**
 * Unified-diff → two-column row model for the SideBySideDiff component.
 *
 * Pure functions — unit-testable without JSX. Pairing rule mirrors GitHub /
 * opencode: consecutive `-` lines then consecutive `+` lines zip into
 * modification rows; leftovers become one-sided rows. Context lines repeat
 * on both columns. Line numbers track the hunk header.
 */

export type CellKind = "ctx" | "del" | "add" | "mod" | "empty";

export interface ColumnCell {
  no: number | null;
  text: string;
  kind: CellKind;
}

export interface DiffRow {
  left: ColumnCell;
  right: ColumnCell;
}

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Parse a unified diff string into side-by-side rows (no trailing clones). */
export function parseUnifiedToRows(diffText: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldNo = 0;
  let newNo = 0;
  let pendingDel: Array<{ no: number; text: string }> = [];
  let pendingAdd: Array<{ no: number; text: string }> = [];

  const flushPending = () => {
    const total = Math.max(pendingDel.length, pendingAdd.length);
    for (let i = 0; i < total; i++) {
      const del = pendingDel[i];
      const add = pendingAdd[i];
      const kind: CellKind = del && add ? "mod" : del ? "del" : "add";
      rows.push({
        left: del
          ? { no: del.no, text: del.text, kind: add ? "mod" : "del" }
          : { no: null, text: "", kind: "empty" },
        right: add
          ? { no: add.no, text: add.text, kind: del ? "mod" : "add" }
          : { no: null, text: "", kind: "empty" },
      });
      void kind;
    }
    pendingDel = [];
    pendingAdd = [];
  };

  for (const line of diffText.split("\n")) {
    const hunk = HUNK_RE.exec(line);
    if (hunk) {
      flushPending();
      oldNo = Number.parseInt(hunk[1] ?? "0", 10);
      newNo = Number.parseInt(hunk[2] ?? "0", 10);
      continue;
    }
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("\\")) {
      continue;
    }
    const marker = line.charAt(0);
    if (marker === " " || line === "") {
      flushPending();
      rows.push({
        left: { no: oldNo, text: line.slice(1), kind: "ctx" },
        right: { no: newNo, text: line.slice(1), kind: "ctx" },
      });
      oldNo += 1;
      newNo += 1;
    } else if (marker === "-") {
      pendingDel.push({ no: oldNo, text: line.slice(1) });
      oldNo += 1;
    } else if (marker === "+") {
      pendingAdd.push({ no: newNo, text: line.slice(1) });
      newNo += 1;
    } else {
      // hdr / stops
      flushPending();
    }
  }
  flushPending();
  return rows;
}

/** Cap rows for the overlay renderer (depth control with a marker row). */
export function capRows(rows: DiffRow[], maxRows: number): DiffRow[] {
  if (rows.length <= maxRows) return rows;
  const head = rows.slice(0, maxRows);
  head.push({
    left: { no: null, text: `… (${rows.length - maxRows} more diff rows)`, kind: "empty" },
    right: { no: null, text: "", kind: "empty" },
  });
  return head;
}
