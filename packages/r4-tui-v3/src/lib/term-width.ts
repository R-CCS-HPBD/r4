/**
 * Shared last-known terminal / chat-column / detail-pane widths.
 *
 * Most of the console historically sized text with hardcoded column caps
 * (truncate(x, 36/56/72/96/110), WRAP_WIDTH = 110, fixed width={28} panels),
 * so wide terminals wasted half their space while narrow ones overflowed.
 * This module is the single source of truth for "how wide is the surface
 * right now":
 *
 *   - the root Shell syncs it from useTerminalDims() (lib/terminal-dims) on every resize
 *     and every pane open/close (createEffect — reactive);
 *   - pure helpers (tool-activity, approval-preview, …) read it
 *     synchronously without needing Solid;
 *   - components that want reactive re-flow derive widths from
 *     useTerminalDims() + chatColumnWidth()/paneColumnWidth() directly.
 *
 * Fallback chain when nothing synced yet (tests, headless spawns):
 * last synced value → process.stdout.columns → 120. At the 120 fallback the
 * derived caps land within a few columns of the legacy hardcoded values, so
 * behavior stays continuous for existing tests and narrow terminals.
 */

import { paneLayout, resolvePaneCols } from "./pane-layout";

let lastTermWidth = 0;
let lastChatWidth = 0;
let lastPaneWidth = 0;

/** Sync the real terminal width (cols). Called by the root shell on resize. */
export function syncTermWidth(width: number): void {
  if (Number.isFinite(width) && width > 0) lastTermWidth = Math.floor(width);
}

/** Sync the chat column width (terminal minus docked pane, when any). */
export function syncChatWidth(width: number): void {
  if (Number.isFinite(width) && width > 0) lastChatWidth = Math.floor(width);
}

/** Sync the detail-pane content width (0 when the pane is closed). */
export function syncPaneWidth(width: number): void {
  if (Number.isFinite(width) && width >= 0) lastPaneWidth = Math.floor(width);
}

/** Test hook: reset all synced widths so fallback chains re-engage. */
export function resetTermWidthForTest(): void {
  lastTermWidth = 0;
  lastChatWidth = 0;
  lastPaneWidth = 0;
}

// Read through globalThis: this module is also type-checked by r4-web
// (browser build, no Node types), where `process` is not declared.
type NodeLikeGlobal = { process?: { stdout?: { columns?: number } } };

function stdoutColumns(): number {
  const cols = (globalThis as NodeLikeGlobal).process?.stdout?.columns;
  return typeof cols === "number" && Number.isFinite(cols) && cols > 0 ? cols : 0;
}

/** Terminal width in columns (synced → stdout.columns → 120). */
export function termWidth(): number {
  return lastTermWidth > 0 ? lastTermWidth : stdoutColumns() || 120;
}

/** Chat column width — falls back to the full terminal width. */
export function chatWidth(): number {
  return lastChatWidth > 0 ? lastChatWidth : termWidth();
}

/** Detail-pane width — falls back to the full terminal width (sheet-like). */
export function paneWidth(): number {
  return lastPaneWidth > 0 ? lastPaneWidth : termWidth();
}

/** Terminal width minus horizontal chrome (padding/borders/prefixes). */
export function contentWidth(chrome = 0, floor = 40): number {
  return Math.max(floor, termWidth() - chrome);
}

/** Chat column width minus horizontal chrome. */
export function chatContentWidth(chrome = 0, floor = 40): number {
  return Math.max(floor, chatWidth() - chrome);
}

/** Detail-pane content width minus chrome (pane border+padding ≈ 4). */
export function paneContentWidth(chrome = 4, floor = 24): number {
  return Math.max(floor, paneWidth() - chrome);
}

/**
 * Content width for a panel occupying `fraction` of the detail pane
 * (e.g. the 33%/34% bottom-panel columns, the 30% verify column).
 */
export function panePanelWidth(fraction = 1, chrome = 10, floor = 20): number {
  return Math.max(floor, Math.floor(paneContentWidth(4, floor) * fraction) - (chrome - 4));
}

/** Chat column for a terminal width + pane state (pure; reactive-friendly).
 * `paneOverride` is workspace.paneWidthOverride (the dragged pane width):
 * Shell docks the pane at resolvePaneCols(override), so the chat column is
 * the remainder — ignoring it made prose wrap to the DEFAULT dock width and
 * a resized pane silently cut words from every row (stabilization
 * 2026-10-21, wip-eval#1). `measured` is the docked pane's laid-out box
 * (pane-measure paneBounds()): when it is current — it ends at this
 * terminal's right edge — its left edge IS the chat column (until c3-tui
 * D-2 the engine shrank the percentage dock to ~49 of 160 cols; DetailPane
 * now sets flexShrink=0, but the measurement stays the source of truth).
 * Stale bounds (closed pane, old terminal size) fail that check and fall
 * back to the estimate. */
export function chatColumnWidth(
  termCols: number,
  paneOpen: boolean,
  paneOverride: number | null = null,
  measured: { x: number; width: number } = { x: 0, width: 0 },
): number {
  if (!paneOpen) return termCols;
  const layout = paneLayout(termCols);
  if (layout === "sheet") return termCols;
  if (measured.x > 0 && measured.x + measured.width === termCols) return measured.x;
  if (paneOverride !== null) return termCols - resolvePaneCols(termCols, paneOverride);
  return Math.floor(termCols * (layout === "dock45" ? 0.55 : 0.62));
}

/** Detail-pane column for a terminal width + pane state (pure). */
export function paneColumnWidth(termCols: number, paneOpen: boolean): number {
  if (!paneOpen) return 0;
  const layout = paneLayout(termCols);
  if (layout === "sheet") return termCols;
  return Math.floor(termCols * (layout === "dock45" ? 0.45 : 0.38));
}
