/**
 * Large-paste chips: the pure tracker lives in `paste-tracker.ts` (ADR-103
 * D-3, 2026-11-01); this module re-exports it and keeps the terminal-width
 * helpers the console's composer needs (Bun.stringWidth).
 */

export {
  createPasteTracker,
  PASTE_CHIP_MIN_CHARS,
  PASTE_CHIP_MIN_LINES,
  type PasteTracker,
} from "./paste-tracker";

/** String index of a caret `cols` display columns into `text` (the input
 * reports its cursor in columns; CJK/emoji are 2 wide). Pastes are spliced
 * at this index, not appended at the end (composer#7). */
export function caretIndex(text: string, cols: number): number {
  let width = 0;
  let index = 0;
  for (const { segment } of new Intl.Segmenter().segment(text)) {
    if (width >= cols) break;
    width += Bun.stringWidth(segment);
    index += segment.length;
  }
  return index;
}

/** Length of the common prefix of two strings (where an edit happened). */
export function commonPrefixLength(a: string, b: string): number {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  return i;
}
