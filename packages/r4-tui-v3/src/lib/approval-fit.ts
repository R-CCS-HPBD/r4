/**
 * Approval card on a short terminal (c3-tui 2026-10-21, item 5).
 *
 * The card never shrank (flexShrink 0) and the chat panel clips at its
 * bottom, so below ~26 rows a long command pushed the key legend, the
 * [Y]es/[N]o rows and the composer off screen: at 12 rows only the first
 * four command lines showed and nothing said which keys answer. When the
 * card's natural height does not fit, the card goes TIGHT: it takes the
 * chat panel's whole body (the transcript collapses, the composer and its
 * hint rows yield — the card is a modal input layer, the composer has no
 * keys meanwhile), the command (and diff) rows scroll in the space left,
 * and the one-line key legend stays pinned under them.
 */

import { createSignal } from "solid-js";

/** Rows the chat column spends outside the card in its normal layout: tab
 * strip, status bar, panel padding (2), title, the gaps around the
 * transcript and card (2), the hint row and its gap (2), the composer (3). */
export const CHAT_CHROME_ROWS = 13;
/** Transcript rows the normal layout keeps beside the card at minimum. */
export const MIN_TRANSCRIPT_ROWS = 2;

/** Does a card of `cardRows` natural rows need the tight layout? */
export function approvalNeedsTight(termRows: number, cardRows: number): boolean {
  return cardRows > termRows - CHAT_CHROME_ROWS - MIN_TRANSCRIPT_ROWS;
}

/** Natural rows of the card: padding (2) + heading + extra info rows +
 * the context block (capped, + its marker row) + the inline diff block +
 * legend (2 with margin) + option rows (1 margin + one per offered answer:
 * once / always / reject → [Y] [A] [N]) + margin below. */
export function approvalCardRows(parts: {
  extraRows: number;
  previewRows: number;
  previewCap: number;
  diffRows: number;
  optionCount: number;
}): number {
  const preview = Math.min(parts.previewRows, parts.previewCap) + (parts.previewRows > parts.previewCap ? 1 : 0);
  const options = 1 + parts.optionCount;
  return 2 + 1 + parts.extraRows + preview + parts.diffRows + 2 + options + 1;
}

const [tight, setTight] = createSignal(false);

/** True while a pending approval card renders in its tight layout. */
export function approvalTight(): boolean {
  return tight();
}

/** The card publishes its layout (idempotent). */
export function publishApprovalTight(value: boolean): void {
  if (tight() !== value) setTight(value);
}
