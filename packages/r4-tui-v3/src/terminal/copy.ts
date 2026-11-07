/**
 * Wrap-aware copy for the terminal pane (pure core, 2026-11-05).
 *
 * The pane paints each xterm row as its own renderable (terminal-pane.tsx
 * `<For each={rows()}>`), and OpenTUI's Selection.getSelectedText() joins
 * renderables with "\n" — so a soft-wrapped long line (a minted JWT in the
 * 2026-11-04 auth incident) copied WITH embedded newlines and pasted in
 * fragments. xterm knows the truth: IBufferLine.isWrapped marks a row that
 * CONTINUES the previous row's logical line. This module turns screen
 * coordinates + the pane's rect into buffer coordinates and extracts text
 * that honours the wrap flags. `/term share` (captureText) joins the same
 * way, so a wrapped line in the captured tail also stays one line.
 */

/** The buffer-line shape this module reads (a subset of IBufferLine). */
export interface CopyBufferLine {
  /** True when this row continues the previous row's logical line (soft wrap). */
  readonly isWrapped: boolean;
  translateToString(trimRight?: boolean, startColumn?: number, endColumn?: number): string;
}

/** The buffer shape this module reads (a subset of IBuffer). */
export interface CopyBufferSource {
  readonly length: number;
  getLine(y: number): CopyBufferLine | undefined;
}

export interface ScreenPoint {
  readonly x: number;
  readonly y: number;
}

/** The pane's rows area in screen cells (row i of the buffer view = pane.y + i). */
export interface PaneRect {
  readonly x: number;
  readonly y: number;
  readonly cols: number;
  readonly rows: number;
}

/** A selection span, normalized top-down. startX applies on topY, endX on bottomY. */
export interface ScreenSpan {
  readonly topY: number;
  readonly bottomY: number;
  readonly startX: number;
  readonly endX: number;
}

/**
 * A selection in pane-relative buffer-view coordinates. Columns follow
 * OpenTUI's own convention, verified against the real renderer 2026-11-05
 * (drag 2→6 over ASCII selects indexes 2..5): startCol inclusive,
 * endCol EXCLUSIVE.
 */
export interface PaneSelection {
  readonly startRow: number;
  readonly endRow: number;
  readonly startCol: number;
  readonly endCol: number;
}

/** Normalize a drag (any direction) into a top-down, left-right span. */
export function normalizeSpan(anchor: ScreenPoint, focus: ScreenPoint): ScreenSpan {
  if (anchor.y !== focus.y) {
    const [first, last] = anchor.y < focus.y ? [anchor, focus] : [focus, anchor];
    return { topY: first.y, bottomY: last.y, startX: first.x, endX: last.x };
  }
  const [left, right] = anchor.x <= focus.x ? [anchor, focus] : [focus, anchor];
  return { topY: left.y, bottomY: left.y, startX: left.x, endX: right.x };
}

/**
 * Map a screen span into the pane, or null unless the WHOLE span lies inside
 * the rows area. A drag that touches anything besides terminal rows stays
 * with OpenTUI's text: mixing re-derived buffer text with renderable text
 * would reorder content. Losing wrap-awareness on a mixed drag is honest;
 * corrupting the order is not.
 */
export function paneSelection(span: ScreenSpan, pane: PaneRect): PaneSelection | null {
  if (span.topY < pane.y || span.bottomY >= pane.y + pane.rows) return null;
  const clamp = (x: number, hi: number) => Math.min(Math.max(x - pane.x, 0), hi);
  return {
    startRow: span.topY - pane.y,
    endRow: span.bottomY - pane.y,
    startCol: clamp(span.startX, pane.cols - 1),
    endCol: clamp(span.endX, pane.cols),
  };
}

/** One captured buffer row as text + its wrap flag (true = continues the row above). */
export interface CaptureRow {
  readonly text: string;
  readonly wrapped: boolean;
}

/** Join captured rows: a newline only where the next row is NOT a continuation. */
export function rowsToText(rows: readonly CaptureRow[]): string {
  let out = rows[0]?.text ?? "";
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    out += row.wrapped ? row.text : `\n${row.text}`;
  }
  return out;
}

/** The slice of one row the selection covers (columns: start inclusive, end exclusive). */
function selectionPiece(line: CopyBufferLine | undefined, sel: PaneSelection, row: number): string {
  if (!line) return "";
  if (row === sel.startRow && row === sel.endRow) {
    return line.translateToString(true, sel.startCol, sel.endCol);
  }
  if (row === sel.startRow) return line.translateToString(true, sel.startCol);
  if (row === sel.endRow) return line.translateToString(true, 0, sel.endCol);
  return line.translateToString(true);
}

/**
 * Extract the selection's text from the buffer, wrap-aware. Buffer line of
 * pane row r = topLine + r (the caller's visible top: baseY - scrollOffset,
 * or 0 on the alternate screen).
 */
export function extractWrapAware(
  source: CopyBufferSource,
  topLine: number,
  sel: PaneSelection,
): string {
  const rows: CaptureRow[] = [];
  for (let row = sel.startRow; row <= sel.endRow; row++) {
    const line = source.getLine(topLine + row);
    rows.push({ text: selectionPiece(line, sel, row), wrapped: line?.isWrapped ?? false });
  }
  return rowsToText(rows);
}
