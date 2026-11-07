/**
 * Responsive detail-pane layout.
 *
 * The V4 pane is 45% docked right of chat on wide terminals; it degrades
 * honestly instead of crushing the chat column:
 *   ≥ 140 cols  → docked at 45%
 *   100–139     → docked at 38%
 *   < 100       → full-width *sheet* covering chat (Esc closes; status line
 *                 notes `pane: sheet`)
 */

export type PaneLayout = "dock45" | "dock38" | "sheet";

export const PANE_LAYOUT_WIDE_MIN = 140;
export const PANE_LAYOUT_NARROW_MIN = 100;

export function paneLayout(terminalWidth: number): PaneLayout {
  if (terminalWidth >= PANE_LAYOUT_WIDE_MIN) return "dock45";
  if (terminalWidth >= PANE_LAYOUT_NARROW_MIN) return "dock38";
  return "sheet";
}

/** Fraction string for docked mode; "100%" for the sheet. */
export function paneFraction(layout: PaneLayout): `${number}%` {
  if (layout === "dock45") return "45%";
  if (layout === "dock38") return "38%";
  return "100%";
}

/** Drag-resize bounds for the detail pane (cols). */
export const PANE_MIN_COLS = 24;
export const PANE_MAX_CHAT_RESERVE = 40;

/** Effective pane width in columns.
 *
 * The percentage default is NOT a reliable source of truth (the flexbox
 * engine's resolved width differs from what percentage math predicts —
 * that mismatch was the command-output overlap: the PTY was sized by
 * formula while CSS painted another width). Callers that need the EXACT
 * size should measure the laid-out box; this helper is the fallback for
 * drag arithmetic and non-rendering budgets, clamped so the chat keeps at
 * least PANE_MAX_CHAT_RESERVE columns.
 * Sheet mode always spans the terminal.
 */
export function resolvePaneCols(termCols: number, overrideCols: number | null): number {
  const layout = paneLayout(termCols);
  if (layout === "sheet") return termCols;
  const max = Math.max(PANE_MIN_COLS, termCols - PANE_MAX_CHAT_RESERVE);
  if (overrideCols !== null) {
    if (!Number.isFinite(overrideCols)) return PANE_MIN_COLS;
    return Math.min(Math.max(PANE_MIN_COLS, Math.floor(overrideCols)), max);
  }
  const fallback = Math.floor(termCols * (layout === "dock45" ? 0.45 : 0.38));
  return Math.min(Math.max(PANE_MIN_COLS, fallback), max);
}
