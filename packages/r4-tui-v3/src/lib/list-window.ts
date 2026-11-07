/**
 * Bounded scrolling window for picker-style lists (pure).
 *
 * The command palette renders a fixed-height panel; without a window,
 * rows beyond the panel are clipped AND unreachable even by keyboard —
 * the selection highlight walked off into invisible rows (user report
 * 2026-09-11). Edge-follow semantics mirror the slash picker.
 */

export interface ListWindow {
  /** First visible row index (global). */
  start: number;
  /** One past the last visible row index (global). */
  end: number;
  /** Clamped offset to persist for the next adjustment. */
  offset: number;
}

/**
 * Clamp `offset` and slide it just enough to keep `selected` visible.
 * Pure: the caller owns the offset signal and feeds it back in.
 */
export function adjustListWindow(
  total: number,
  selected: number,
  offset: number,
  cap: number,
): ListWindow {
  const safeCap = Math.max(1, cap);
  const maxOffset = Math.max(0, total - safeCap);
  let next = Math.min(Math.max(offset, 0), maxOffset);
  if (selected < next) next = Math.max(0, selected);
  else if (selected >= next + safeCap) next = Math.min(maxOffset, selected - safeCap + 1);
  const start = next;
  const end = Math.min(total, start + safeCap);
  return { start, end, offset: next };
}
