/**
 * Word-wrap for transcript prose (visibility wave, 2026-10-19).
 *
 * Before this, MarkdownBlock painted every source line as ONE terminal row
 * (height=1, overflow=hidden), so a list item longer than the chat column
 * was hard-clipped at the right edge — the continuation simply vanished
 * from the transcript. Here one source line becomes as many wrapped rows
 * as the column needs; nothing is dropped, and nothing wraps mid-word
 * unless the token itself is wider than the column.
 *
 * Scope: prose lines only. Code-fence and table rows keep their
 * horizontal-clip contract (MarkdownBlock routes around them).
 */

/** Unordered (`- `) / ordered (`1. ` or `1) `) bullet prefixes. */
const LIST_MARKER = /^(\s*(?:[-*+]|\d+[.)]) )/;
/** Blockquote prefixes (`>`, `> `, nested `> > `). */
const QUOTE_MARKER = /^(\s*>[ >]*)/;

/** Spaces aligning wrapped continuation rows under the list/quote text.
 * A marker eating half the column is degenerate — fall back to no indent. */
function hangingPad(line: string, width: number): string {
  const marker = LIST_MARKER.exec(line)?.[1] ?? QUOTE_MARKER.exec(line)?.[1] ?? null;
  if (!marker || marker.length >= Math.floor(width / 2)) return "";
  return " ".repeat(marker.length);
}

/** Columns a string paints in the terminal (wide CJK / full-width = 2).
 * Stabilization 2026-10-21 (wip-eval#2): the wrap used to count UTF-16
 * code units, so Japanese rows came out twice the column width and their
 * tails were clipped away. */
export function displayWidth(text: string): number {
  return Bun.stringWidth(text);
}

const GRAPHEMES = new Intl.Segmenter("ja", { granularity: "grapheme" });

function graphemes(text: string): string[] {
  return Array.from(GRAPHEMES.segment(text), (s) => s.segment);
}

/** Longest prefix of `text` painting at most `cols` columns (a wide
 * grapheme that would straddle the limit is left out). */
export function sliceColumns(text: string, cols: number): string {
  let used = 0;
  let out = "";
  for (const ch of graphemes(text)) {
    used += displayWidth(ch);
    if (used > cols) break;
    out += ch;
  }
  return out;
}

/** Kinsoku: never start a row with closing punctuation / prolonged sound
 * mark, never end one with opening brackets. */
const NO_ROW_START = new Set(Array.from("、。，．・：；？！ー…」』）〕］｝〉》】）"));
const NO_ROW_END = new Set(Array.from("「『（〔［｛〈《【"));

/** May a row end before chars[i]? At a space (the space is dropped), or
 * between two graphemes where either side is wide — CJK prose has no
 * spaces, so wide-character boundaries are its break opportunities. */
function canBreakBefore(chars: string[], widths: number[], i: number): boolean {
  const prev = chars[i - 1];
  const cur = chars[i];
  if (cur === " ") return true;
  if (prev === " " || NO_ROW_START.has(cur) || NO_ROW_END.has(prev)) return false;
  return widths[i - 1] >= 2 || widths[i] >= 2;
}

/** End index of the row starting at `start`: the last break opportunity
 * within `max` columns (never `start`), or a hard cut when the head is one
 * unbroken token. Scans one row only, so a wrap stays linear in the line. */
function cutIndex(chars: string[], widths: number[], start: number, max: number): number {
  let cols = 0;
  let lastBreak = start;
  for (let i = start; i < chars.length; i++) {
    if (i > start && canBreakBefore(chars, widths, i)) lastBreak = i;
    cols += widths[i];
    if (cols > max) return lastBreak > start ? lastBreak : Math.max(start + 1, i);
  }
  return chars.length;
}

function sumWidths(widths: number[], from: number, to: number): number {
  let total = 0;
  for (let i = from; i < to; i++) total += widths[i];
  return total;
}

/**
 * Wrap ONE prose source line into terminal rows each <= `width` display
 * columns. Short lines and widths too small to wrap meaningfully pass
 * through as-is (one row), so callers can apply this unconditionally.
 * Linear in the line (c3-tui 2026-10-21): re-measuring the whole rest per
 * row made one 580 KB unbroken paste take 11 s to wrap.
 */
export function wrapProseLine(line: string, width: number): string[] {
  if (width < 8 || displayWidth(line) <= width) return [line];
  const pad = hangingPad(line, width);
  const rows: string[] = [];
  const chars = graphemes(line);
  const widths = chars.map(displayWidth);
  let remaining = sumWidths(widths, 0, chars.length);
  let start = 0;
  let max = width;
  while (remaining > max) {
    const cut = cutIndex(chars, widths, start, max);
    rows.push((rows.length > 0 ? pad : "") + chars.slice(start, cut).join(""));
    const next = chars[cut] === " " ? cut + 1 : cut;
    remaining -= sumWidths(widths, start, next);
    start = next;
    max = width - pad.length;
  }
  rows.push((rows.length > 0 ? pad : "") + chars.slice(start).join(""));
  return rows;
}
