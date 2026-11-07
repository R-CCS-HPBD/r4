/**
 * Horizontal marquee for over-long single-line labels (chat tab titles).
 *
 * Ping-pong scrolling with a dwell at both ends: text holds at the start,
 * slides left until the tail is visible, holds, then slides back. The window
 * never wraps the text around (a wrap looks like a second label in a tab
 * strip). Pure functions — rendered cells just advance a tick counter.
 */

import { displayWidth, sliceColumns } from "./markdown-wrap";

export interface MarqueeOptions {
  /** Hold time at each end of the travel, in milliseconds. */
  dwellMs: number;
  /** Scroll speed, columns per second. */
  pxPerSec: number;
}

export const MARQUEE_DEFAULTS: MarqueeOptions = { dwellMs: 1400, pxPerSec: 10 };

/** Scroll offset (columns) for one point in time; 0 when the text fits. */
export function marqueeOffset(
  textLen: number,
  cap: number,
  tickMs: number,
  opts: MarqueeOptions = MARQUEE_DEFAULTS,
): number {
  const travel = textLen - cap;
  if (travel <= 0 || cap <= 0) return 0;
  const speed = Math.max(1, opts.pxPerSec);
  const travelMs = (travel / speed) * 1000;
  const dwell = Math.max(0, opts.dwellMs);
  const leg = travelMs + dwell;
  const period = leg * 2;
  const t = ((tickMs % period) + period) % period;
  if (t < dwell) return 0;
  if (t < leg) return Math.min(travel, Math.floor(((t - dwell) / travelMs) * travel));
  const back = t - leg;
  if (back < dwell) return travel;
  return Math.max(0, travel - Math.floor(((back - dwell) / travelMs) * travel));
}

/** `text` without its first `cols` display columns (a wide glyph the cut
 * would halve goes too, so the window never starts on half a glyph). */
function dropColumns(text: string, cols: number): string {
  let dropped = 0;
  let index = 0;
  for (const ch of text) {
    if (dropped >= cols) break;
    dropped += displayWidth(ch);
    index += ch.length;
  }
  return text.slice(index);
}

/** The visible window of `text` at `tickMs` (cap terminal columns).
 * Measured in display columns, not UTF-16 units (c2-tui 2026-10-21): a
 * Japanese title paints two columns per glyph, came out twice the cell and
 * wrapped inside the one-row tab cell. */
export function marqueeSlice(
  text: string,
  cap: number,
  tickMs: number,
  opts: MarqueeOptions = MARQUEE_DEFAULTS,
): string {
  if (cap <= 0) return "";
  const width = displayWidth(text);
  if (width <= cap) return text;
  const off = marqueeOffset(width, cap, tickMs, opts);
  return sliceColumns(dropColumns(text, off), cap);
}

/** Static overflow marker for cells that must not animate (background tabs). */
export function ellipsisSlice(text: string, cap: number): string {
  if (displayWidth(text) <= cap) return text;
  if (cap <= 1) return sliceColumns(text, Math.max(0, cap));
  return `${sliceColumns(text, cap - 1)}…`;
}
