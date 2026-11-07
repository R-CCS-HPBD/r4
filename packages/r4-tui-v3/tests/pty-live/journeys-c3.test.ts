/**
 * pty-live journeys, stabilization cycle 3 (c3-tui, 2026-10-21): the real dev
 * TUI in a real PTY against the offline fixture bridge (fixture-bridge/).
 * The live repro scripts live in the lane scratch dir ($C3/tui).
 *
 * Opt-in like the rest of the tier: R4_PTY_LIVE=1 bun test tests/pty-live/journeys-c3.test.ts
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 120_000;

async function boot(opts: { rows?: number; cols?: number } = {}): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, ...opts });
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

/** A pasted log of `n` lines (~60 bytes each), sent as one bracketed paste. */
function pasteLog(tui: TuiProcess, n: number, tag: string): void {
  const body = Array.from({ length: n }, (_, i) => `2026-10-21T10:00:00 INFO worker-${i % 16} processed batch ${i} ok`);
  tui.send(`\x1b[200~${body.join("\n")}\n# ${tag}\x1b[201~`);
}

suite("pty journeys, cycle 3 (offline fixture bridge)", () => {
  it(
    "D-1: a 10,000-line paste after a 2,000-line turn is sent, painted, and the composer stays live",
    async () => {
      const tui = await boot({ rows: 40, cols: 120 });
      try {
        pasteLog(tui, 2_000, "first-paste");
        await expectScreen(tui, "[Pasted text #1");
        tui.send(KEYS.enter);
        await expectScreen(tui, "prior turns: 0)", 20_000);
        pasteLog(tui, 10_000, "second-paste");
        await expectScreen(tui, "[Pasted text #2");
        tui.send(KEYS.enter);
        // Base: the chip stayed in the composer, nothing reached the bridge
        // and the transcript never changed.
        const frame = await expectScreen(tui, "prior turns: 1)", 20_000);
        expect(frame).toContain("# second-paste");
        expect(frame).not.toContain("[Pasted text #2");
        tui.send("typed-after");
        await expectScreen(tui, "typed-after", 5_000);
      } finally {
        await tui.snap("journey-c3-huge-paste");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );

  it(
    "item 5: on a 12-row terminal the approval card keeps its key legend and scrolls the command",
    async () => {
      const tui = await boot({ rows: 12, cols: 100 });
      try {
        tui.send("fx approve long");
        await expectScreen(tui, "fx approve long", 5_000);
        await tui.pump(150);
        tui.send(KEYS.enter);
        // Base: only lines 1-4 showed and no key legend.
        const card = await expectScreen(tui, "y/Enter once", 15_000);
        expect(card).toContain("long-cmd-line-1");
        for (let k = 0; k < 20; k++) {
          tui.send(KEYS.down);
          await tui.pump(60);
        }
        const scrolled = await expectScreen(tui, "long-cmd-line-18", 5_000);
        expect(scrolled).toContain("y/Enter once"); // ADR-104 tight legend
        tui.send("y");
        await expectScreen(tui, "›", 10_000);
      } finally {
        await tui.snap("journey-c3-approval-short");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );
});
