/**
 * pty-live journeys, stabilization cycle 2 (c2-tui, 2026-10-21): the real dev
 * TUI in a real PTY against the offline fixture bridge (fixture-bridge/).
 * Each journey pins one cycle-2 defect end to end; the live-stack repro
 * scripts live in the lane scratch dir ($C2/tui).
 *
 * Opt-in like the rest of the tier: R4_PTY_LIVE=1 bun test tests/pty-live/journeys-c2.test.ts
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 120_000;
const CTRL_UP = "\x1b[1;5A";
const PGUP = "\x1b[5~";
const PGDN = "\x1b[6~";

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true });
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) tui.send(KEYS.esc);
  await tui.waitFor("Chat with R4", 20_000);
  await tui.waitFor("workspace ready", 20_000);
  return tui;
}

async function expectScreen(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const frame = await tui.waitFor(text, timeoutMs);
  if (!frame.includes(text)) throw new Error(`"${text}" never rendered; last frame:\n${frame}`);
  return frame;
}

/** Type, wait for the echo, Enter, wait for the fixture's reply count. */
async function turn(tui: TuiProcess, text: string, prior: number): Promise<void> {
  tui.send(text);
  await expectScreen(tui, text, 5_000);
  await tui.pump(150);
  tui.send(KEYS.enter);
  await expectScreen(tui, `prior turns: ${prior})`, 15_000);
}

/** Everything below the tab strip (the strip repeats the first prompt). */
function body(frame: string): string {
  return frame.split("\n").slice(1).join("\n");
}

function lowestTurnShown(frame: string): number {
  const shown = [...body(frame).matchAll(/turn-(\d\d)/g)].map((m) => Number(m[1]));
  return shown.length ? Math.min(...shown) : -1;
}

function sleepAlive(marker: string): boolean {
  const out = Bun.spawnSync(["pgrep", "-f", marker]).stdout.toString().trim();
  return out.length > 0;
}

suite("pty journeys, cycle 2 (offline fixture bridge)", () => {
  it(
    "long session: the transcript cursor keeps ▸ on screen; PgUp/PgDn page the transcript",
    async () => {
      const tui = await boot();
      try {
        for (let i = 1; i <= 14; i++) await turn(tui, `turn-${String(i).padStart(2, "0")}`, i - 1);
        await tui.pump(500);
        const bottom = tui.screen.dump();
        expect(body(bottom)).toContain("turn-14");
        expect(lowestTurnShown(bottom)).toBeGreaterThan(1);
        // PgUp pages back (only the wheel scrolled before), PgDn returns.
        for (let k = 0; k < 8; k++) {
          tui.send(PGUP);
          await tui.pump(200);
        }
        expect(lowestTurnShown(tui.screen.dump())).toBeLessThan(lowestTurnShown(bottom));
        for (let k = 0; k < 12; k++) {
          tui.send(PGDN);
          await tui.pump(200);
        }
        expect(body(tui.screen.dump())).toContain("turn-14");
        // Ctrl+↑ selects the newest message; every ↑ must keep ▸ visible
        // (it vanished after two presses on the base).
        tui.send(CTRL_UP);
        await expectScreen(tui, "▸");
        let hidden = 0;
        for (let k = 0; k < 30; k++) {
          tui.send(KEYS.up);
          await tui.pump(150);
          if (!tui.screen.dump().includes("▸")) hidden++;
        }
        expect(hidden).toBe(0);
        tui.send(KEYS.esc);
        await tui.pump(250);
      } finally {
        await tui.snap("journey-c2-long-session");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );

  it(
    "`!` output with control sequences paints them visibly, never as live escapes",
    async () => {
      const tui = await boot();
      try {
        tui.send("!printf 'pre \\033]0;FXTITLE\\007 \\033[31mred\\033[0m post-printf\\n'");
        await tui.pump(400);
        tui.send(KEYS.enter);
        // The rendered output (the composer still echoing the command
        // spells \033, never ␛): SGR dropped, OSC shown as visible pictures.
        const frame = await expectScreen(tui, "red post-printf", 15_000);
        expect(frame).toContain("␛]0;FXTITLE␇");
      } finally {
        await tui.snap("journey-c2-terminal-safe");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );

  it(
    "quitting while a `!` command runs leaves no orphan behind",
    async () => {
      const marker = `sleep ${300 + (process.pid % 97)}`;
      const tui = await boot();
      let killed = false;
      try {
        tui.send(`!${marker}`);
        await tui.pump(400);
        tui.send(KEYS.enter);
        for (let i = 0; i < 50 && !sleepAlive(marker); i++) await tui.pump(100);
        expect(sleepAlive(marker)).toBe(true);
        await tui.kill(); // SIGTERM → safeExit
        killed = true;
        for (let i = 0; i < 40 && sleepAlive(marker); i++) await Bun.sleep(100);
        expect(sleepAlive(marker)).toBe(false);
      } finally {
        if (!killed) await tui.kill();
        Bun.spawnSync(["pkill", "-f", marker]);
      }
    },
    JOURNEY_MS,
  );
});
