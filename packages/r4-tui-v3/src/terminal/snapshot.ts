/**
 * Terminal pane: xterm buffer → OpenTUI styled rows (pure).
 *
 * Reads the @xterm/headless buffer and produces one StyledText per visible
 * row so the component can paint a live shell with colors without owning a
 * VT parser itself. Cell colors resolve honestly:
 *   - RGB cells   → their hex value
 *   - 256 palette → the standard xterm cube/gray table
 *   - 16 palette  → the classic VGA table
 *   - default fg  → the caller's default (the pane text color, so the shell
 *                   follows the console theme), default bg → transparent
 *                   (the pane tone shows through)
 */

import { StyledText, type TextChunk } from "@opentui/core";
import { fg, bg, bold, italic, underline, strikethrough, dim, blink, reverse } from "@opentui/core";

/** One buffer cell, as read from @xterm/headless. */
export interface SnapshotCell {
  getChars(): string;
  getWidth(): number;
  getFgColorMode(): number;
  getBgColorMode(): number;
  getFgColor(): number;
  getBgColor(): number;
  isBold(): number;
  isItalic(): number;
  isDim(): number;
  isUnderline(): number;
  isBlink(): number;
  isInverse(): number;
  isStrikethrough(): number;
}

/** The buffer-shaped subset of @xterm/headless this module reads. */
export interface BufferSnapshotSource {
  length: number;
  getLine(y: number):
    | { length: number; getCell(x: number): SnapshotCell | undefined }
    | undefined;
}

const VGA16 = [
  "#000000", "#cd0000", "#00cd00", "#cdcd00", "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
  "#7f7f7f", "#ff0000", "#00ff00", "#ffff00", "#5c5cff", "#ff00ff", "#00ffff", "#ffffff",
];

/** Lookup cache for the 256-color palette (cube + grayscale ramp). */
const PALETTE_256: string[] = (() => {
  const out = VGA16.slice();
  const levels = [0, 95, 135, 175, 215, 255];
  for (let r = 0; r < 6; r++)
    for (let g = 0; g < 6; g++)
      for (let b = 0; b < 6; b++)
        out.push(`#${hex2(levels.at(r) ?? 0)}${hex2(levels.at(g) ?? 0)}${hex2(levels.at(b) ?? 0)}`);
  for (let i = 0; i < 24; i++) {
    const v = 8 + i * 10;
    out.push(`#${hex2(v)}${hex2(v)}${hex2(v)}`);
  }
  return out;
})();

function hex2(v: number): string {
  return v.toString(16).padStart(2, "0");
}

/** Resolve one cell's fg/bg hex pair (undefined = theme default/transparent). */
function cellColors(cell: SnapshotCell, defaultFg: string): { fgHex: string; bgHex: string | undefined } {
  const fgMode = cell.getFgColorMode();
  const bgMode = cell.getBgColorMode();
  // modes: 0 default, 1 = 16-color palette, 2 = 256-color palette, 3 = RGB.
  const fgHex =
    fgMode === 0 ? defaultFg : fgMode === 1 ? VGA16.at(cell.getFgColor() & 0x0f) ?? defaultFg : fgMode === 2
      ? PALETTE_256.at(cell.getFgColor()) ?? defaultFg
      : `#${hex2((cell.getFgColor() >> 16) & 0xff)}${hex2((cell.getFgColor() >> 8) & 0xff)}${hex2(cell.getFgColor() & 0xff)}`;
  const bgHex =
    bgMode === 0 ? undefined : bgMode === 1 ? VGA16.at(cell.getBgColor() & 0x0f) : bgMode === 2
      ? PALETTE_256.at(cell.getBgColor())
      : `#${hex2((cell.getBgColor() >> 16) & 0xff)}${hex2((cell.getBgColor() >> 8) & 0xff)}${hex2(cell.getBgColor() & 0xff)}`;
  return { fgHex, bgHex };
}

interface StyleKey {
  fgHex: string;
  bgHex: string | undefined;
  bold: boolean;
  italic: boolean;
  dim: boolean;
  under: boolean;
  blinky: boolean;
  strike: boolean;
  inverse: boolean;
}

function chunkFor(text: string, s: StyleKey): TextChunk {
  let base: string | TextChunk = text;
  if (s.bold) base = bold(base);
  if (s.italic) base = italic(base);
  if (s.dim) base = dim(base);
  if (s.under) base = underline(base);
  if (s.blinky) base = blink(base);
  if (s.strike) base = strikethrough(base);
  if (s.inverse) base = reverse(base);
  if (s.bgHex) base = bg(s.bgHex)(base);
  base = fg(s.fgHex)(base);
  return base as TextChunk;
}

export interface SnapshotOptions {
  cols: number;
  rows: number;
  /** Absolute buffer line of the first visible row (0 when live). */
  top: number;
  /**
   * Cursor position (absolute line). `block` = focused: solid color block
   * (the user asked for an unmissable focus signal — the plain reverse
   * attribute was nearly invisible over theme-fg cells). `hollow` =
   * unfocused: text intact over a raised tone (there IS a shell cursor,
   * it just isn't listening).
   */
  cursor?: { x: number; y: number; style: "block" | "hollow" } | null;
  /** Theme text color for default-fg cells. */
  defaultFg: string;
  /** Filled-block colors: background = the cell's own fg, text = this tone. */
  blockFg: string;
  /** Hollow-cursor tone for the unfocused state. */
  hollowBg: string;
}

/** Build `rows` styled rows starting at buffer line `top`. */
export function snapshotRows(buffer: BufferSnapshotSource, opts: SnapshotOptions): StyledText[] {
  const out: StyledText[] = [];
  for (let r = 0; r < opts.rows; r++) {
    out.push(snapshotRow(buffer, opts.top + r, opts));
  }
  return out;
}

function snapshotRow(
  buffer: BufferSnapshotSource,
  lineIndex: number,
  opts: SnapshotOptions,
): StyledText {
  const line = buffer.getLine(lineIndex);
  if (!line) return new StyledText([]);
  const cursorX = cursorColumn(opts, lineIndex);
  const row = new RowChunks();
  const last = lastFilledColumn(line, opts.cols, cursorX);
  for (let x = 0; x <= last; x++) {
    const cell = line.getCell(x);
    if (!cell) break;
    if (cell.getWidth() === 0) continue; // wide-char continuation cell
    // A blank cell inside the line is a space: a tab stop or a cursor jump
    // leaves cells with no chars (`ls` columns, right prompts), and painting
    // them as "" glued the columns together (2026-10-25).
    row.add(cellStyle(cell, x === cursorX, opts), cell.getChars() || " ");
  }
  return row.finish();
}

/** The cursor's column on this line, or -1. */
function cursorColumn(opts: SnapshotOptions, lineIndex: number): number {
  return opts.cursor?.y === lineIndex ? opts.cursor.x : -1;
}

/** Runs of same-styled cells become one chunk. */
class RowChunks {
  private chunks: TextChunk[] = [];
  private current: StyleKey | null = null;
  private text = "";

  add(style: StyleKey, chars: string): void {
    if (this.current && sameStyle(this.current, style)) {
      this.text += chars;
      return;
    }
    this.flush();
    this.current = style;
    this.text = chars;
  }

  finish(): StyledText {
    this.flush();
    return new StyledText(this.chunks);
  }

  private flush(): void {
    if (this.current && this.text.length > 0) this.chunks.push(chunkFor(this.text, this.current));
    this.text = "";
  }
}

/** The last column worth painting: the last cell with chars, or the cursor.
 * Past-EOL cells carry no chars and stay unpainted. */
function lastFilledColumn(
  line: { length: number; getCell(x: number): SnapshotCell | undefined },
  cols: number,
  cursorX: number,
): number {
  for (let x = Math.min(cols, line.length) - 1; x > cursorX; x--) {
    if (line.getCell(x)?.getChars()) return x;
  }
  return cursorX;
}

/** One cell's style; the cursor cell is a solid block (focused) or a
 * hollow tone behind intact text (unfocused). */
function cellStyle(cell: SnapshotCell, isCursor: boolean, opts: SnapshotOptions): StyleKey {
  const { fgHex, bgHex } = cellColors(cell, opts.defaultFg);
  const style: StyleKey = {
    fgHex,
    bgHex,
    bold: Boolean(cell.isBold()),
    italic: Boolean(cell.isItalic()),
    dim: Boolean(cell.isDim()),
    under: Boolean(cell.isUnderline()),
    blinky: Boolean(cell.isBlink()),
    strike: Boolean(cell.isStrikethrough()),
    inverse: Boolean(cell.isInverse()),
  };
  if (!isCursor || !opts.cursor) return style;
  if (opts.cursor.style === "block") {
    // Focused cursor: solid block — cell's own fg becomes the block color.
    return { ...style, bgHex: style.fgHex, fgHex: opts.blockFg, inverse: false };
  }
  // Unfocused cursor: hollow — text intact, raised tone behind.
  return { ...style, bgHex: style.bgHex ?? opts.hollowBg, inverse: false };
}

function sameStyle(a: StyleKey, b: StyleKey): boolean {
  return (
    a.fgHex === b.fgHex &&
    a.bgHex === b.bgHex &&
    a.bold === b.bold &&
    a.italic === b.italic &&
    a.dim === b.dim &&
    a.under === b.under &&
    a.blinky === b.blinky &&
    a.strike === b.strike &&
    a.inverse === b.inverse
  );
}
