/**
 * B2 — Scroll anchoring helpers.
 * stickyScroll already re-engages when the user returns to the bottom; these
 * pure helpers compute the pill state: am I at the bottom (within slack), and
 * how many transcript rows arrived while I was scrolled up.
 */

/** True when the viewport is at (or within `slack` rows of) the bottom. */
export function isAtBottom(
  scrollTop: number,
  viewportHeight: number,
  scrollHeight: number,
  slack = 1,
): boolean {
  return scrollTop + viewportHeight >= scrollHeight - slack;
}

/** New rows that arrived while the user was scrolled up (never negative). */
export function newContentCount(totalRows: number, anchoredRows: number): number {
  return Math.max(0, totalRows - anchoredRows);
}

/** Pill label, e.g. "↓ 3 new — End to jump to latest". */
export function newContentPillLabel(count: number): string {
  return `↓ ${count} new — End to jump to latest`;
}
