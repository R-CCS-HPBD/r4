/**
 * Minimal VT100 screen buffer for scraping an OpenTUI PTY stream.
 * OpenTUI paints with absolute cursor addressing (\x1b[{r};{c}H + text), so
 * contiguous-line scraping cannot reconstruct frames — this grid can.
 */

/** CSI parameter `idx`, or `dflt` when absent or zero (VT100 rule). */
function arg(p: number[], idx: number, dflt: number): number {
  const value = p[idx];
  return value !== undefined && value > 0 ? value : dflt;
}

/** A sequence head with nothing after it yet: lone ESC, an unfinished CSI
 * (no final byte), an OSC string without its BEL / ST terminator, or a
 * charset designation without its charset. */
const INCOMPLETE_ESCAPE = /^\x1b(?:\[[0-9;?<>=]*[ -\/]*|\][^\x07\x1b]*\x1b?|[()])?$/;
/** An unterminated sequence longer than this is parsed as it stands. */
const MAX_PENDING = 4096;

export class VtScreen {
  readonly rows: number;
  readonly cols: number;
  private grid: string[][];
  private r = 0;
  private c = 0;

  constructor(rows: number, cols: number) {
    this.rows = rows;
    this.cols = cols;
    this.grid = Array.from({ length: rows }, () => Array<string>(cols).fill(" "));
  }

  /** One streaming decoder: a UTF-8 sequence split across PTY chunks
   * (box glyphs, ▸, ●) must not turn into two U+FFFD cells. */
  private readonly decoder = new TextDecoder("utf-8", { fatal: false });

  /** An escape sequence the previous chunk ended inside (see `escape`). */
  private pending = "";

  feed(data: Uint8Array | string): void {
    const text = this.pending + (typeof data === "string" ? data : this.decoder.decode(data, { stream: true }));
    this.pending = "";
    let i = 0;
    while (i < text.length) {
      // Whole code points: an astral glyph (emoji) is one cell write, not two halves.
      const ch = String.fromCodePoint(text.codePointAt(i)!);
      const used = ch === "\x1b" ? this.escape(text.slice(i)) : this.control(ch);
      if (used === 0) {
        this.pending = text.slice(i);
        return;
      }
      i += used;
    }
  }

  /** Apply one non-escape character; returns its UTF-16 length. */
  private control(ch: string): number {
    if (ch === "\r") this.c = 0;
    else if (ch === "\n") this.r = Math.min(this.rows - 1, this.r + 1);
    else if (ch === "\b") this.c = Math.max(0, this.c - 1);
    else if (ch.charCodeAt(0) >= 32) this.print(ch);
    return ch.length;
  }

  /** Wide glyphs (CJK, most emoji) take TWO cells like a real terminal: the
   * second is an empty continuation. Advancing one column left the cell a
   * wide glyph covers holding a stale character — the "漢kZabc" ghost of
   * w3-tui item 9 was this emulator, not the TUI (2026-10-21). Zero-width
   * marks join the previous cell. */
  private print(ch: string): void {
    const width = Bun.stringWidth(ch);
    if (width === 0) {
      this.joinPrevious(ch);
      return;
    }
    this.put(this.c, ch);
    if (width === 2) this.put(this.c + 1, "");
    this.c += width;
  }

  private put(col: number, value: string): void {
    if (this.r >= 0 && this.r < this.rows && col >= 0 && col < this.cols) this.grid[this.r]![col] = value;
  }

  private joinPrevious(mark: string): void {
    const row = this.grid[this.r];
    if (!row || this.c <= 0) return;
    let col = this.c - 1;
    while (col > 0 && row[col] === "") col--;
    row[col] = (row[col] ?? "") + mark;
  }

  /**
   * Consume one escape sequence at the head of `rest`; returns its length.
   * CSI with intermediates (`ESC [ 1 SP q`, cursor style) and OSC strings
   * (`ESC ] 12 ; #rrggbb BEL`, cursor colour) are swallowed whole — they
   * used to leak their tails ("1 q", "12;#…") into the composer row.
   * Returns 0 when `rest` ends inside a sequence: the PTY cut it between two
   * chunks, and `feed` keeps it for the next one (the "3try listing instead"
   * flake, 2026-10-21).
   */
  private escape(rest: string): number {
    if (rest.length < MAX_PENDING && INCOMPLETE_ESCAPE.test(rest)) return 0;
    const csi = /^\x1b\[([0-9;?<>=]*)[ -\/]*([@-~])/.exec(rest);
    if (csi) {
      this.csi(csi[1] ?? "", csi[2]!);
      return csi[0].length;
    }
    const osc = /^\x1b\][^\x07\x1b]*(\x07|\x1b\\)?/.exec(rest);
    if (osc) return osc[0].length;
    const other = /^\x1b[\(\)][0-9A-B]|^\x1b./.exec(rest);
    return other ? other[0].length : 1;
  }

  /** Cursor moves and erases OpenTUI emits; anything else is ignored. */
  private readonly csiHandlers: Record<string, (p: number[]) => void> = {
    H: (p) => this.moveTo(p),
    f: (p) => this.moveTo(p),
    A: (p) => (this.r = Math.max(0, this.r - arg(p, 0, 1))),
    B: (p) => (this.r = Math.min(this.rows - 1, this.r + arg(p, 0, 1))),
    C: (p) => (this.c = Math.min(this.cols - 1, this.c + arg(p, 0, 1))),
    D: (p) => (this.c = Math.max(0, this.c - arg(p, 0, 1))),
    J: (p) => this.eraseDisplay(p),
    K: (p) => this.eraseLine(p),
  };

  private csi(params: string, cmd: string): void {
    const p = params
      .replace("?", "")
      .split(";")
      .filter((x) => x !== "")
      .map((x) => Number.parseInt(x, 10) || 0);
    this.csiHandlers[cmd]?.(p);
  }

  private moveTo(p: number[]): void {
    this.r = arg(p, 0, 1) - 1;
    this.c = arg(p, 1, 1) - 1;
  }

  private eraseDisplay(p: number[]): void {
    if (p.length === 0 || p[0] === 2 || p[0] === 3) {
      this.grid = Array.from({ length: this.rows }, () => Array<string>(this.cols).fill(" "));
    }
  }

  private eraseLine(p: number[]): void {
    const mode = p[0] ?? 0;
    const row = this.grid[this.r]!;
    const [from, to] = mode === 0 ? [this.c, this.cols] : mode === 1 ? [0, this.c + 1] : [0, this.cols];
    for (let x = from; x < to; x++) row[x] = " ";
  }

  dump(): string {
    return this.grid.map((row) => row.join("").replace(/\s+$/, "")).join("\n");
  }
}
