/**
 * pty-live journey (wave 72, 2026-10-24): `/workflow graph`, the status-bar
 * workflow badge and Alt+W, in the real dev TUI against the fixture bridge
 * (fixture-bridge/server.ts knows one running execution, fxwf-run-0001 of
 * fx-pipeline, and its five-step definition).
 *
 * Opt-in like the rest of the tier:
 * `R4_PTY_LIVE=1 bun test tests/pty-live/journeys-workflow-graph.test.ts`.
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 90_000;
const OVERLAY = "workflow execution — Esc closes";
const BADGE = "▶ 1 workflow · ◐ 2/5";
const ALT_W = "\x1bw";

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, rows: 40, cols: 140 });
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

async function expectGone(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = tui.screen.dump();
  while (frame.includes(text) && Date.now() < deadline) {
    await tui.pump(120);
    frame = tui.screen.dump();
  }
  if (frame.includes(text)) throw new Error(`"${text}" still rendered; last frame:\n${frame}`);
  return frame;
}

/** Type a prompt, wait for the echo (the slash bar drops the "/"), press Enter. */
async function submit(tui: TuiProcess, text: string): Promise<void> {
  tui.send(text);
  await expectScreen(tui, text.replace(/^\//, ""), 5_000);
  await tui.pump(150);
  tui.send(KEYS.enter);
}

suite("pty journey: /workflow graph, the workflow badge and Alt+W", () => {
  it(
    "no runs → /work hint; an id opens the live DAG and lights the badge; Esc; Alt+W reopens it",
    async () => {
      const tui = await boot();
      try {
        // Nothing runs: no badge, Alt+W says so, the verb points to /work.
        expect(tui.screen.dump()).not.toContain("▶");
        tui.send(ALT_W);
        await expectScreen(tui, "no workflow execution is running");
        await submit(tui, "/workflow graph");
        await expectScreen(tui, "no workflow executions in this session yet");
        await tui.snap("journey-wfgraph-empty");

        // An unknown id is named back.
        await submit(tui, "/workflow graph nope-42");
        await expectScreen(tui, "unknown workflow execution: nope-42");

        // A UMRS id: read once, tracked, overlay with the definition's DAG.
        await submit(tui, "/workflow graph fxwf-run-0001");
        await expectScreen(tui, OVERLAY);
        const open = await expectScreen(tui, "graph (5 steps");
        expect(open).toContain("fx-pipeline");
        await expectScreen(tui, BADGE);
        await tui.snap("journey-wfgraph-overlay");

        tui.send(KEYS.esc);
        await tui.pump(200);
        const closed = await expectGone(tui, OVERLAY);
        expect(closed).toContain(BADGE);
        await tui.snap("journey-wfgraph-badge");

        // Alt+W: the badge's run, from anywhere.
        tui.send(ALT_W);
        await expectScreen(tui, OVERLAY);
        await expectScreen(tui, "fxwf-run-000");
      } finally {
        await tui.snap("journey-wfgraph-final");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );
});
