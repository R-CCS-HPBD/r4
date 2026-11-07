/**
 * One /sessions pane row, fitted to the pane's MEASURED inner width
 * (c3-tui 2026-10-21, D-2). The row's cells used to be sized from the
 * estimated pane width (panePanelWidth): the name cell alone asked for
 * about the whole pane, Yoga squeezed every cell to fit, and the chips
 * painted over each other ("Fcompletmirror10-21"). Here the cells are
 * budgeted from the real width: the name yields first, then the sync
 * badge, then the state chip; the id, venue glyph and date always stay.
 */

import { displayWidth, sliceColumns } from "./markdown-wrap";

export interface SessionRowInput {
  id: string;
  name: string;
  pinned?: boolean;
  archived_at?: string;
  updated_at: string;
  state?: string;
  sync?: string;
}

export interface SessionRowCells {
  /** `★ id8  name` — the name cut to what is left. */
  head: string;
  state: string;
  sync: string;
  date: string;
}

/** Columns the pane's chrome takes from its measured width: the left bar
 * plus one column of padding on each side (DetailPane padding={1}). */
export const PANE_CHROME_COLS = 3;

/** `MM-DD HH:MM` from an ISO stamp (shorter stamps pass through). */
export function sessionRowDate(updatedAt: string): string {
  return updatedAt.length >= 16 ? updatedAt.slice(5, 16).replace("T", " ") : updatedAt;
}

/** `text` in at most `cols` display columns, `…` marking a cut. */
export function fitColumns(text: string, cols: number): string {
  if (cols <= 0) return "";
  if (displayWidth(text) <= cols) return text;
  return `${sliceColumns(text, cols - 1)}…`;
}

/** Width of the cells after the head: glyph, each chip, the date, and one
 * gap column before each of them. */
function tailCols(cells: string[]): number {
  return cells.filter(Boolean).reduce((sum, cell) => sum + 1 + displayWidth(cell), 0);
}

/** Name columns the chips yield to before the name is cut further. */
export const NAME_MIN_COLS = 12;

/** Drop the sync badge, then the state chip, while they would leave the
 * name fewer than NAME_MIN_COLS columns (`room` = columns after the id). */
function fittedChips(record: SessionRowInput, date: string, room: number): { state: string; sync: string } {
  let state = record.state ?? "";
  let sync = record.sync ?? "";
  if (tailCols(["G", state, sync, date]) > room) sync = "";
  if (tailCols(["G", state, sync, date]) > room) state = "";
  return { state, sync };
}

/** The row's cells for a pane `innerCols` wide (the glyph is one column). */
export function sessionRowCells(record: SessionRowInput, innerCols: number): SessionRowCells {
  const id = `${record.pinned ? "★ " : ""}${record.id.slice(0, 8)}`;
  const date = `${sessionRowDate(record.updated_at)}${record.archived_at ? " ⛁" : ""}`;
  const { state, sync } = fittedChips(record, date, innerCols - displayWidth(id) - 2 - NAME_MIN_COLS);
  const nameRoom = innerCols - displayWidth(id) - 2 - tailCols(["G", state, sync, date]);
  const head = nameRoom >= 4 ? `${id}  ${fitColumns(record.name, nameRoom)}` : id;
  return { head: fitColumns(head, Math.max(1, innerCols - tailCols(["G", date]))), state, sync, date };
}
