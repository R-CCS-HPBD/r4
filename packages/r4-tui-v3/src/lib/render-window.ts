/**
 * Row window for one huge transcript block (c3-tui 2026-10-21, D-1).
 *
 * Every rendered transcript row is at least one native text renderable, and
 * OpenTUI walks EVERY renderable on every frame (the lifecycle pass), not
 * only the visible ones. A 2,000-line paste already cost ~15 % CPU while
 * idle; a second 10,000-line (580 KB) paste never painted: the console
 * stopped repainting, typed keys never showed, the draft was never cleared
 * and a resize then failed to allocate a frame buffer and exited. A block
 * past RENDER_ROW_CAP rows therefore renders its first and last rows plus
 * one marker row; the marker pages more rows in, up to a hard ceiling. The
 * stored message is never touched — the model gets every byte, and the
 * transcript cursor's `c` still copies all of it.
 */

/** Rows a block renders in full; past this it collapses to a preview. */
export const RENDER_ROW_CAP = 400;
/** Leading rows the collapsed preview keeps. */
export const PREVIEW_HEAD_ROWS = 120;
/** Trailing rows the collapsed preview keeps. */
export const PREVIEW_TAIL_ROWS = 40;
/** Rows one "show more" adds to the head. */
export const EXPAND_PAGE_ROWS = 1000;
/** Ceiling of rows one block ever renders, however often it is expanded. */
export const RENDER_ROW_HARD_CAP = 4000;

/** How a block's items count and split into rows. */
export interface RowSlicer<T> {
  rows(item: T): number;
  /** The item cut to its own rows [from, to). */
  take(item: T, from: number, to: number): T;
}

export interface RowWindow<T> {
  head: T[];
  tail: T[];
  /** Rows between head and tail that are not rendered (0: nothing hidden). */
  hiddenRows: number;
  totalRows: number;
  /** True when another page would still fit under the hard cap. */
  canExpand: boolean;
}

function takeFirst<T>(items: T[], s: RowSlicer<T>, budget: number): T[] {
  const out: T[] = [];
  let left = budget;
  for (const item of items) {
    if (left <= 0) break;
    const rows = s.rows(item);
    out.push(rows <= left ? item : s.take(item, 0, left));
    left -= rows;
  }
  return out;
}

function takeLast<T>(items: T[], s: RowSlicer<T>, budget: number): T[] {
  const out: T[] = [];
  let left = budget;
  for (let i = items.length - 1; i >= 0 && left > 0; i--) {
    const rows = s.rows(items[i]!);
    out.unshift(rows <= left ? items[i]! : s.take(items[i]!, rows - left, rows));
    left -= rows;
  }
  return out;
}

/** Head row budget after `pages` expansions (never past the hard cap). */
export function headBudget(pages: number): number {
  const wanted = PREVIEW_HEAD_ROWS + Math.max(0, pages) * EXPAND_PAGE_ROWS;
  return Math.min(wanted, RENDER_ROW_HARD_CAP - PREVIEW_TAIL_ROWS);
}

/** The rows of `items` to render: all of them up to RENDER_ROW_CAP, else a
 * head (grown by `pages`) + tail preview around the hidden middle. */
export function windowRows<T>(items: T[], s: RowSlicer<T>, pages = 0): RowWindow<T> {
  const totalRows = items.reduce((sum, item) => sum + s.rows(item), 0);
  const head = headBudget(pages);
  if (totalRows <= RENDER_ROW_CAP || totalRows <= head + PREVIEW_TAIL_ROWS) {
    return { head: items, tail: [], hiddenRows: 0, totalRows, canExpand: false };
  }
  return {
    head: takeFirst(items, s, head),
    tail: takeLast(items, s, PREVIEW_TAIL_ROWS),
    hiddenRows: totalRows - head - PREVIEW_TAIL_ROWS,
    totalRows,
    canExpand: head < headBudget(pages + 1),
  };
}

/** The marker row between a collapsed block's head and tail. */
export function hiddenRowsLabel(hiddenRows: number, canExpand: boolean): string {
  const n = hiddenRows.toLocaleString("en-US");
  const more = canExpand
    ? `click to show ${Math.min(EXPAND_PAGE_ROWS, hiddenRows).toLocaleString("en-US")} more`
    : `the transcript shows at most ${RENDER_ROW_HARD_CAP.toLocaleString("en-US")} rows of one message`;
  return `⋯ ${n} rows hidden — ${more} · Ctrl+↑ then c copies the whole message`;
}

/** True when `text` has more source lines than RENDER_ROW_CAP. The colored
 * diff views render every line, so a diff this long takes the windowed
 * markdown path instead (uncolored, but it paints). */
export function exceedsRowCap(text: string): boolean {
  let lines = 1;
  for (let at = text.indexOf("\n"); at >= 0; at = text.indexOf("\n", at + 1)) {
    if (++lines > RENDER_ROW_CAP) return true;
  }
  return false;
}
