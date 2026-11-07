/**
 * Terminal pane: the one PTY session + its xterm/headless emulation layer.
 *
 * One shared shell per console process (v1). The session is created lazily
 * when the pane opens and SURVIVES the pane closing — `/term` reattaches to
 * the same buffer, `/term kill` ends it. App exit kills it through the
 * registered exit cleanup.
 *
 * PTY spawn rides Bun's `terminal` spawn option (runtime ≥1.3.0; the types
 * in @types/bun 1.3.13 predate it, so the spawn boundary is typed locally
 * and cast once). Escape-sequence parsing is @xterm/headless; rendering is
 * snapshot.ts.
 */

import { createSignal } from "solid-js";
import { Terminal as XtermTerminal } from "@xterm/headless";
import { snapshotRows, type BufferSnapshotSource } from "./snapshot";
import { wrapBracketedPaste } from "./keys";
import {
  extractWrapAware,
  normalizeSpan,
  paneSelection,
  rowsToText,
  type CaptureRow,
  type CopyBufferSource,
  type PaneRect,
  type ScreenPoint,
} from "./copy";

interface PtyTerminalIo {
  write(data: string | Uint8Array): void;
  resize(cols: number, rows: number): void;
  close(): void;
}

interface PtyProcLike {
  terminal?: PtyTerminalIo;
  exited: Promise<number>;
  kill(exitCode?: number): void;
}

type PtySpawn = (cmd: string[], opts: Record<string, unknown>) => PtyProcLike;

export type TermState = "idle" | "running" | "exited" | "unsupported";

export interface TermStatus {
  state: TermState;
  exitCode?: number;
  /** Why "unsupported": the host cannot run a local PTY shell. */
  reason?: string;
}

const SCROLLBACK_LINES = 2000;

/** Lifecycle trace for PTY-debug runs (R4_TUI_TERM_DEBUG=1 → stderr file). */
function termDebug(event: string, detail: Record<string, unknown> = {}): void {
  if (process.env.R4_TUI_TERM_DEBUG !== "1") return;
  const path = process.env.R4_TUI_TERM_DEBUG_FILE ?? "/tmp/r4-term-debug.log";
  const line = `${new Date().toISOString()} ${event} ${JSON.stringify(detail)}\n`;
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    fs.appendFileSync(path, line);
  } catch {
    // debug logging must never break the app
  }
}

export class TerminalSession {
  private xterm: XtermTerminal | null = null;
  private proc: PtyProcLike | null = null;
  private listeners = new Set<() => void>();
  cols = 80;
  rows = 24;
  state: TermState = "idle";
  exitCode: number | undefined;
  /** Rows scrolled back from the live bottom (0 = live). */
  scrollOffset = 0;
  /** The pane's rows area in screen cells — registered by terminal-pane.tsx
   * on every layout read so selection coordinates can be mapped onto buffer
   * rows for the wrap-aware copy (copy.ts). Null while the pane is closed. */
  private paneRect: PaneRect | null = null;

  /** Solid revision token — bumped after every buffer/state change. */
  readonly revision: () => number;
  private readonly bump: (fn: (n: number) => number) => void;

  constructor() {
    const [revision, setRevision] = createSignal(0);
    this.revision = revision;
    this.bump = (fn) => setRevision(fn);
  }

  status(): TermStatus {
    if (this.state === "unsupported") {
      return { state: this.state, reason: "a local PTY shell needs a POSIX host (this console is on win32)" };
    }
    return this.state === "exited"
      ? { state: this.state, exitCode: this.exitCode }
      : { state: this.state };
  }

  /**
   * Plain-text capture of the tail of the buffer for `/term share` — the
   * last `maxLines` rows with trailing blanks trimmed (interior blanks
   * stay; the capture must be honest). Soft-wrapped rows rejoin into ONE
   * logical line (IBufferLine.isWrapped), so a wrapped URL/token in the
   * tail survives intact. Returns null when there is no session/buffer at
   * all — the caller must never relay an empty frame.
   */
  captureText(maxLines: number): string | null {
    const buf = this.xterm?.buffer.active;
    if (!buf) return null;
    // Anchor at the LAST non-empty line (viewport rows below the prompt are
    // blank runtime cells, not content), then collect maxLines above it.
    let end = buf.length - 1;
    while (end >= 0 && (buf.getLine(end)?.translateToString(true) ?? "").trim() === "") end--;
    const rows: CaptureRow[] = [];
    for (let y = end; y >= 0 && rows.length < maxLines; y--) {
      const line = buf.getLine(y);
      rows.unshift({
        text: line?.translateToString(true) ?? "",
        wrapped: line?.isWrapped ?? false,
      });
    }
    termDebug("capture", { lines: rows.length });
    return rowsToText(rows);
  }

  /** Register the pane's rows-area rect (screen cells); null clears it. */
  setPaneRect(rect: PaneRect | null): void {
    this.paneRect = rect;
  }

  /** The buffer as the copy core reads it (wrap flags + column slicing). */
  get copySource(): CopyBufferSource | null {
    return this.xterm ? (this.xterm.buffer.active as unknown as CopyBufferSource) : null;
  }

  /**
   * Wrap-aware text for a screen-coordinate drag selection, or null when the
   * selection is not entirely inside the pane's rows area (the caller then
   * uses OpenTUI's own selected text — a mixed chat+terminal drag never
   * mixes the two coordinate models).
   */
  copySelectionText(anchor: ScreenPoint, focus: ScreenPoint): string | null {
    const source = this.copySource;
    if (!source || !this.paneRect) return null;
    const sel = paneSelection(normalizeSpan(anchor, focus), this.paneRect);
    if (!sel) return null;
    const top = this.alternateActive
      ? 0
      : Math.max(0, (this.xtermBaseY() ?? 0) - this.scrollOffset);
    return extractWrapAware(source, top, sel);
  }

  get buffer(): BufferSnapshotSource | null {
    return this.xterm ? (this.xterm.buffer.active as unknown as BufferSnapshotSource) : null;
  }

  get applicationCursorKeys(): boolean {
    return this.xterm?.modes.applicationCursorKeysMode ?? false;
  }

  get bracketedPaste(): boolean {
    return this.xterm?.modes.bracketedPasteMode ?? false;
  }

  /** Alternate screen (vim/htop) — scrollback makes no sense there. */
  get alternateActive(): boolean {
    return this.bufferType() === "alternate";
  }

  private bufferType(): string {
    if (!this.xterm) return "normal";
    return (this.xterm.buffer.active as { type?: string }).type ?? "normal";
  }

  cursorPosition(): { x: number; y: number } | null {
    if (!this.xterm || this.state !== "running") return null;
    const buf = this.xterm.buffer.active;
    return { x: buf.cursorX, y: buf.baseY + buf.cursorY };
  }

  xtermBaseY(): number | null {
    return this.xterm ? this.xterm.buffer.active.baseY : null;
  }

  onData(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Spawn when idle/exited; resize to fit when already running. */
  ensure(cols: number, rows: number): void {
    cols = Math.max(4, Math.floor(cols));
    rows = Math.max(2, Math.floor(rows));
    if (this.state === "running") {
      if (cols !== this.cols || rows !== this.rows) this.resize(cols, rows);
      return;
    }
    if (this.state === "unsupported") return;
    termDebug("respawn", { cols, rows, state: this.state });
    this.spawn(cols, rows);
  }

  private spawn(cols: number, rows: number): void {
    if (process.platform === "win32") {
      this.state = "unsupported";
      this.notify();
      return;
    }
    this.cols = cols;
    this.rows = rows;
    this.scrollOffset = 0;
    this.xterm = new XtermTerminal({
      cols,
      rows,
      scrollback: SCROLLBACK_LINES,
      allowProposedApi: true,
    });
    const shell = process.env.SHELL?.trim() || "/bin/sh";
    const cwd = process.env.R4_TUI_WORKSPACE?.trim() || process.cwd();
    const spawn = Bun.spawn as unknown as PtySpawn;
    this.proc = spawn([shell], {
      cwd,
      env: {
        ...process.env,
        TERM: "xterm-256color",
        COLORTERM: "truecolor",
        R4_TERMINAL: "1",
      },
      terminal: {
        cols,
        rows,
        data: (_terminal: unknown, data: Uint8Array) => this.onBytes(data),
      },
    });
    this.state = "running";
    this.exitCode = undefined;
    termDebug("spawn", { cols, rows, cwd });
    this.proc.exited.then((code) => {
      this.state = "exited";
      this.exitCode = code;
      this.proc = null;
      this.notify();
    });
    this.notify();
  }

  private onBytes(data: Uint8Array): void {
    if (!this.xterm) {
      // Late bytes after a kill — never crash a dying frame for them.
      return;
    }
    this.scrollOffset = 0;
    // Notify AFTER the parser flush (xterm defers parsing to the write
    // callback); bursts merge because every chunk schedules at most one
    // trailing notify.
    this.xterm.write(data, () => {
      if (this.state === "idle") return; // killed mid-flush
      this.notify();
    });
  }

  /** Bytes the user typed (already translated by keys.ts). */
  write(bytes: string): void {
    if (this.state !== "running") return;
    this.proc?.terminal?.write(bytes);
  }

  /** Paste payloads honour the shell's bracketed-paste mode. */
  paste(bytes: string): void {
    this.write(wrapBracketedPaste(bytes, this.bracketedPaste));
  }

  resize(cols: number, rows: number): void {
    cols = Math.max(4, Math.floor(cols));
    rows = Math.max(2, Math.floor(rows));
    if (cols === this.cols && rows === this.rows) return;
    termDebug("resize", { from: `${this.cols}x${this.rows}`, to: `${cols}x${rows}`, state: this.state });
    this.cols = cols;
    this.rows = rows;
    this.xterm?.resize(cols, rows);
    if (this.state === "running") this.proc?.terminal?.resize(cols, rows);
    this.notify();
  }

  /** Scroll the VIEW back (never affects the shell). steps are in rows. */
  scrollBy(steps: number): void {
    if (this.alternateActive) return;
    const buf = this.xterm?.buffer.active;
    const max = buf ? buf.baseY : 0;
    this.scrollOffset = Math.min(Math.max(0, this.scrollOffset + steps), max);
    this.notify();
  }

  restart(): void {
    this.kill();
    this.ensure(this.cols, this.rows);
  }

  kill(): void {
    const proc = this.proc;
    this.proc = null;
    try {
      proc?.terminal?.close();
    } catch {
      // pty already closed — best effort
    }
    try {
      proc?.kill();
    } catch {
      // already dead — best effort
    }
    termDebug("kill", { had: Boolean(proc) });
    this.state = "idle";
    this.xterm = null;
    this.scrollOffset = 0;
    this.notify();
  }

  private notify(): void {
    this.bump((n) => n + 1);
    for (const listener of [...this.listeners]) listener();
  }
}

/** The console's singleton shell. */
export const termSession = new TerminalSession();

/** Render `rows` styled rows from the current buffer/view state. */
export function visibleRows(opts: {
  rows: number;
  cols: number;
  defaultFg: string;
  /** Pane background tone — the block cursor's text color. */
  blockFg: string;
  /** Raised tone for the hollow (unfocused) cursor. */
  hollowBg: string;
  /** Focused = solid block cursor; unfocused = hollow. */
  focused: boolean;
}) {
  const buffer = termSession.buffer;
  if (!buffer) return null;
  const baseY = termSession.alternateActive
    ? 0
    : Math.max(0, (termSession.xtermBaseY() ?? 0) - termSession.scrollOffset);
  const pos = termSession.cursorPosition();
  const cursor = pos ? { ...pos, style: opts.focused ? ("block" as const) : ("hollow" as const) } : null;
  return snapshotRows(buffer, {
    cols: opts.cols,
    rows: opts.rows,
    top: baseY,
    cursor,
    defaultFg: opts.defaultFg,
    blockFg: opts.blockFg,
    hollowBg: opts.hollowBg,
  });
}
