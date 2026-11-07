/**
 * pty-live: the /term Terminal pane — a real PTY shell docked in the TUI.
 *
 * Offline-safe (the shell is local; no backend dependency). Gated like the
 * rest of this tier: run with R4_PTY_LIVE=1.
 *
 * Covers (2026-10-03 revision):
 *   - opens UNFOCUSED (composer keeps keys; title carries the click hint)
 *   - click focuses, click in chat releases (only pointer moves focus)
 *   - output alignment stays true (the measured-width fix: no formula/
 *     CSS mismatch wrap class)
 *   - dragging the pane's left bar resizes it, and the shell follows
 *   - Esc closes when unfocused; /term reattaches; /term kill tears down
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const LIVE = process.env.R4_PTY_LIVE === "1";
const suite = LIVE ? describe : describe.skip;

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) {
    tui.send(KEYS.esc);
  }
  await tui.waitFor("Chat with R4", 20_000);
  return tui;
}

/** 1-based SGR coordinates of the pane's left bar and an inside point. */
function paneCoords(frame: string): { barX: number; innerX: number; rowY: number } {
  const lines = frame.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines.at(i) ?? "";
    const titleIdx = line.indexOf("Terminal");
    if (titleIdx < 0) continue;
    const bar0 = line.lastIndexOf("┃", titleIdx - 1);
    return { barX: bar0 + 1, innerX: bar0 + 8, rowY: i + 1 + 3 };
  }
  throw new Error("pane title row not found in frame");
}

function click(x: number, y: number): string[] {
  // SGR extended mouse: press then immediate release.
  return [`\x1b[<0;${x};${y}M`, `\x1b[<0;${x};${y}m`];
}

function drag(fromX: number, fromY: number, toX: number): string[] {
  // Three-step drag: a column-fused rapid stream of motion events desyncs
  // the driver-side SGR parser (drops the next keystroke + leaks a digit) —
  // real terminals emit spaced events; mirror that cadence.
  const seq = [`\x1b[<0;${fromX};${fromY}M`];
  const mid1 = fromX + Math.floor((toX - fromX) / 3);
  const mid2 = fromX + Math.floor(((toX - fromX) * 2) / 3);
  seq.push(`\x1b[<32;${mid1};${fromY}M`);
  seq.push(`\x1b[<32;${mid2};${fromY}M`);
  seq.push(`\x1b[<0;${toX};${fromY}m`);
  return seq;
}

/** Poll until `text` is GONE from the frame (waitFor only matches presence). */
async function waitGone(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = tui.screen.dump();
  while (frame.includes(text)) {
    if (Date.now() > deadline) return frame;
    await tui.pump(120);
    frame = tui.screen.dump();
  }
  return frame;
}

suite("pty-live terminal pane (offline-safe)", () => {
  it("click-only focus, measured alignment, border drag-resize, reattach, kill", async () => {
    const tui = await boot();
    try {
      // ── Open via the slash verb. Pane opens UNFOCUSED: typing must reach
      // the composer, and the title carries the click hint.
      tui.send("/term");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const open = await tui.waitFor("Terminal · click to focus", 10_000);
      await tui.snap("pty-term-open-unfocused");
      expect(open).toContain("Terminal · click to focus");
      tui.send("probe-unfocused");
      const typed = await tui.waitFor("probe-unfocused", 5_000);
      expect(typed).toContain("probe-unfocused");
      // Clear the probe draft without sending it.
      for (let i = 0; i < 15; i++) tui.send("\x7f");
      await tui.pump(200);

      // ── Click into the pane: focus flips, the hint disappears.
      const coords = paneCoords(await tui.snap("pty-term-before-click"));
      for (const s of click(coords.innerX, coords.rowY)) tui.send(s);
      const focused = await waitGone(tui, "Terminal · click", 8_000);
      await tui.snap("pty-term-clicked-focused");
      expect(focused).not.toContain("Terminal · click");

      // ── Output alignment (the measured-width fix): 25 numbered rows must
      // each occupy their own dump row — the old formula/CSS mismatch
      // truncated and glued rows.
      tui.send("clear");
      tui.send(KEYS.enter);
      await tui.pump(400);
      tui.send("for i in $(seq 1 25); do echo LINE$i; done");
      tui.send(KEYS.enter);
      await tui.pump(800);
      tui.send("echo PTYTERM_$((21*2))");
      tui.send(KEYS.enter);
      const echoed = await tui.waitFor("PTYTERM_42", 15_000);
      await tui.snap("pty-term-aligned-rows");
      expect(echoed).toContain("PTYTERM_42");
      for (let i = 1; i <= 25; i++) expect(echoed).toContain(`LINE${i}`);
      const glue = /LINE\d+\S+LINE\d+/.exec(echoed);
      expect(glue).toBeNull();

      // ── Ctrl+C reaches the shell as SIGINT (never the chat interrupt).
      tui.send("sleep 60");
      await tui.pump(200);
      tui.send("\x03");
      const afterInterrupt = await tui.waitFor("PTYTERM_42", 5_000);
      expect(afterInterrupt).not.toContain("Press Ctrl+C again to exit");

      // ── Esc goes to the shell (vim class) while focused; nothing closes.
      tui.send(KEYS.esc);
      await tui.pump(300);
      expect(await tui.snap("pty-term-esc-in-shell")).toContain("PTYTERM_42");

      // ── Click in the chat column: focus releases, typing reaches chat.
      for (const s of click(6, 6)) tui.send(s);
      const released = await tui.waitFor("Terminal · click to focus", 8_000);
      await tui.snap("pty-term-chat-click-released");
      expect(released).toContain("Terminal · click to focus");
      tui.send("probe-after-release");
      const typed2 = await tui.waitFor("probe-after-release", 5_000);
      expect(typed2).toContain("probe-after-release");
      for (let i = 0; i < 19; i++) tui.send("\x7f");
      await tui.pump(200);

      // ── Unfocused Esc follows the normal pane rule and closes the pane;
      // the shell keeps running behind it.
      tui.send(KEYS.esc);
      await tui.pump(500);
      expect(await tui.snap("pty-term-pane-closed")).not.toContain("Terminal · click to focus");
      tui.send("/term");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const reattached = await tui.waitFor("PTYTERM_42", 10_000);
      await tui.snap("pty-term-reattached");
      expect(reattached).toContain("PTYTERM_42");

      // ── Click back in (drag is NOT a focus click), then drag the bar 12
      // columns left: pane grows, shell follows.
      const preDrag = paneCoords(await tui.snap("pty-term-before-drag"));
      for (const s of click(preDrag.innerX, preDrag.rowY)) tui.send(s);
      await waitGone(tui, "Terminal · click", 8_000);
      for (const s of drag(preDrag.barX, 20, preDrag.barX - 12)) tui.send(s);
      await tui.pump(800);
      const after = paneCoords(await tui.snap("pty-term-after-drag"));
      expect(after.barX).toBeLessThanOrEqual(preDrag.barX - 10);
      // The shell sees the new width: bash maintains COLUMNS on SIGWINCH.
      // Post-drag synthetic streams occasionally desync the driver's SGR
      // parser — the FIRST keystroke after a drag can be eaten as a
      // sequence continuation. Send a sacrificial empty command first.
      await tui.pump(500);
      tui.send(KEYS.enter);
      await tui.pump(300);
      tui.send("echo BCOLS_$((1+1))_$COLUMNS");
      tui.send(KEYS.enter);
      const resized = await tui.waitFor("BCOLS_2_", 8_000);
      await tui.snap("pty-term-resize-cols");
      // pane box starts at the bar (0-based col barX-1) and spans to 140;
      // the terminal box inside drops border(1)+padding(2).
      const expectedCols = 140 - (after.barX - 1) - 3;
      expect(resized).toContain(`BCOLS_2_${expectedCols}`);

      // ── /term share relays the buffer tail to the session agent (never
      // auto-streamed). Marker computed in-output only (echo-line trap).
      tui.send("echo SHAREND_$((12*12))");
      tui.send(KEYS.enter);
      await tui.waitFor("SHAREND_144", 8_000);
      for (const s of click(6, 6)) tui.send(s);
      await tui.waitFor("Terminal · click to focus", 8_000);
      tui.send("/term share 8");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const shared = await tui.waitFor("shared 8 terminal lines", 10_000);
      await tui.snap("pty-term-share");
      expect(shared).toContain("shared 8 terminal lines");
      expect(shared).toContain("--- terminal ---");
      expect(shared).toContain("SHAREND_144");

      // ── Kill closes pane + shell; composer keeps focus for typing.
      // Release focus first (click chat, the click-only contract), then
      // the slash verb reaches the composer.
      tui.send("/term kill");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const killed = await tui.waitFor("terminal shell killed", 8_000);
      await tui.snap("pty-term-killed");
      expect(killed).toContain("terminal shell killed");
      tui.send("focusprobe-term");
      const typedProbe = await tui.waitFor("focusprobe-term", 5_000);
      expect(typedProbe).toContain("focusprobe-term");
    } finally {
      tui.kill();
    }
  }, 180_000);
});
