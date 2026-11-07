/**
 * Terminal pane: live PTY session round trip.
 *
 * Spawns a real shell under Bun's terminal spawn option and verifies output
 * parsing, resize, exit detection, restart and kill. POSIX-only (the engine
 * reports "unsupported" on win32 by design).
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { TerminalSession } from "../src/terminal/pty-session";

const POSIX = process.platform !== "win32";

function bufferText(session: TerminalSession): string {
  const buf = session.buffer;
  if (!buf) return "";
  const lines: string[] = [];
  for (let i = 0; i < buf.length; i++) {
    const line = buf.getLine(i);
    if (!line) continue;
    const cells: string[] = [];
    for (let x = 0; x < line.length; x++) cells.push(line.getCell(x)?.getChars() ?? "");
    lines.push(cells.join(""));
  }
  return lines.join("\n");
}

async function waitFor(cond: () => boolean, timeoutMs = 6000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (cond()) return true;
    await new Promise((r) => setTimeout(r, 60));
  }
  return false;
}

describe.skipIf(!POSIX)("terminal session (live PTY)", () => {
  let session: TerminalSession;
  beforeEach(() => {
    session = new TerminalSession();
  });
  afterEach(() => {
    session.kill();
  });

  test("spawn → type a command → output parses into the buffer", async () => {
    session.ensure(40, 10);
    expect(session.status().state).toBe("running");
    session.write("echo SESS_$((6*7))\n");
    const seen = await waitFor(() => bufferText(session).includes("SESS_42"));
    expect(seen).toBe(true);
  });

  test("resize flows to the shell", async () => {
    session.ensure(40, 10);
    session.resize(60, 12);
    // COLUMNS is bash-only; stty size (rows cols) is POSIX.
    session.write('echo STTY="$(stty size 2>/dev/null)"\n');
    const seen = await waitFor(() => bufferText(session).includes("STTY=12 60"));
    expect(seen).toBe(true);
  });

  test("exit is detected; ensure respawns; kill goes idle", async () => {
    session.ensure(40, 10);
    session.write("exit 3\n");
    const exited = await waitFor(() => session.status().state === "exited");
    expect(exited).toBe(true);
    expect(session.status().exitCode === 3 || session.status().exitCode === 129).toBe(true);
    session.ensure(40, 10);
    expect(session.status().state).toBe("running");
    session.write("echo BACK_$((2+3))\n");
    expect(await waitFor(() => bufferText(session).includes("BACK_5"))).toBe(true);
    session.kill();
    expect(session.status().state).toBe("idle");
    expect(session.buffer).toBe(null);
  });

  test("scrollback accumulates and the view scrolls", async () => {
    session.ensure(60, 6);
    session.write("for i in $(seq 1 12); do echo LINE_$i; done\n");
    expect(await waitFor(() => bufferText(session).includes("LINE_12"))).toBe(true);
    const baseY = session.xtermBaseY() ?? 0;
    expect(baseY).toBeGreaterThan(0);
    session.scrollBy(3); // positive = back into scrollback
    expect(session.scrollOffset).toBe(3);
    session.scrollBy(-99); // past the live tail clamps to 0
    expect(session.scrollOffset).toBe(0);
  });
});
