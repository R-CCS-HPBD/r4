/**
 * Wrap-aware terminal copy — live PTY end to end (2026-11-05).
 *
 * The pane paints each xterm row as its own renderable, and OpenTUI's
 * Selection.getSelectedText() joins renderables with "\n". A soft-wrapped
 * long line (a JWT, a URL) therefore copied WITH embedded newlines — the
 * 2026-11-04 auth incident: a minted token pasted in fragments.
 * xterm knows the truth (IBufferLine.isWrapped marks continuation rows);
 * captureText (/term share) and pane selection copy must honour it.
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { TerminalSession } from "../src/terminal/pty-session";

const POSIX = process.platform !== "win32";
// 108 chars wraps to 3 rows at 40 cols; no spaces, no shell metacharacters.
const MARKER = "WRAPTOK_" + "X".repeat(100);

async function waitFor(cond: () => boolean, timeoutMs = 8000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (cond()) return true;
    await new Promise((r) => setTimeout(r, 60));
  }
  return false;
}

describe.skipIf(!POSIX)("wrap-aware copy (live PTY)", () => {
  let session: TerminalSession;
  beforeEach(() => {
    session = new TerminalSession();
  });
  afterEach(() => {
    session.kill();
  });

  test("captureText keeps a soft-wrapped long line as ONE line", async () => {
    session.ensure(40, 12);
    // The marker must exist ONLY in the output, never in the command echo —
    // a wrap-joined echo would satisfy includes() before output lands.
    session.write(`printf '%s\\n' "$(python3 -c "print('WRAPTOK_'+'X'*100)")"\n`);
    const seen = await waitFor(() => (session.captureText(50) ?? "").includes(MARKER));
    expect(seen).toBe(true);
  });

  test("copySelectionText copies a selection across the wrap as ONE line", async () => {
    session.ensure(40, 12);
    session.setPaneRect({ x: 0, y: 0, cols: 40, rows: 12 });
    session.write(`printf '%s\\n' "$(python3 -c "print('WRAPTOK_'+'X'*100)")"\n`);
    const rowOf = (): number => {
      // The OUTPUT head row is exactly WRAPTOK_ + 32 X (40 cols); an echo
      // continuation row can merely START with the prefix — match the full
      // row, newest first (output follows the echo).
      const src = session.copySource;
      if (!src) return -1;
      for (let y = src.length - 1; y >= 0; y--) {
        if (/^WRAPTOK_X{32}$/.test(src.getLine(y)?.translateToString(true) ?? "")) return y;
      }
      return -1;
    };
    expect(await waitFor(() => rowOf() >= 0)).toBe(true);
    // Buffer lines include SCROLLBACK; a screen row = buffer line − baseY.
    const head = rowOf() - (session.xtermBaseY() ?? 0);
    expect(head).toBeGreaterThanOrEqual(0);
    expect(head + 2).toBeLessThan(12);
    const text = session.copySelectionText({ x: 0, y: head }, { x: 28, y: head + 2 });
    expect(text).toBe(MARKER);
  });

  test("copySelectionText declines selections outside the pane rect", async () => {
    session.ensure(40, 12);
    session.setPaneRect({ x: 5, y: 3, cols: 40, rows: 12 });
    // A span whose top row sits above the rect is not the pane's to copy.
    expect(session.copySelectionText({ x: 5, y: 1 }, { x: 8, y: 6 })).toBeNull();
    expect(session.copySelectionText({ x: 5, y: 16 }, { x: 8, y: 18 })).toBeNull();
  });
});
