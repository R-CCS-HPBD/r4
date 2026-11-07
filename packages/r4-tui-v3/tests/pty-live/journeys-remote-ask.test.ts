/**
 * pty-live journey (ADR-099 D-8, maintainer decision M-6): a remote
 * session's ask_user question reaches the user in any attached client.
 *
 * The real dev TUI in a PTY against fixture-bridge/server.ts with
 * R4_PTY_FIXTURE_VENUE=frontend and R4_PTY_FIXTURE_ASK=remote: the session
 * opens while its worker waits on a question (no turn was sent from this
 * client). The question opens on attach; Esc sets it aside; /venue questions
 * shows it again; the answer reaches session.venue_answer. Opt-in like the
 * rest of this tier: `R4_PTY_LIVE=1 bun test tests/pty-live/journeys-remote-ask.test.ts`.
 */

import { describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 90_000;
const QUESTION = "Which fixture dataset should the remote worker use?";
const TITLE = "R4 (remote session) needs your input";

async function boot(log: string): Promise<TuiProcess> {
  const tui = await spawnTui({
    fixture: true,
    extraEnv: { R4_PTY_FIXTURE_VENUE: "frontend", R4_PTY_FIXTURE_ASK: "remote", R4_PTY_FIXTURE_LOG: log },
  });
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) tui.send(KEYS.esc);
  await tui.waitFor("Chat with R4", 20_000);
  return tui;
}

async function expectScreen(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const frame = await tui.waitFor(text, timeoutMs);
  if (!frame.includes(text)) throw new Error(`"${text}" never rendered; last frame:\n${frame}`);
  return frame;
}

/** The fixture bridge's answer requests (R4_PTY_FIXTURE_LOG trace lines). */
async function answersSent(log: string, timeoutMs = 10_000): Promise<string[]> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    let lines: string[] = [];
    try {
      lines = readFileSync(log, "utf8").split("\n").filter((l) => l.includes('"session.venue_answer"'));
    } catch {
      lines = [];
    }
    if (lines.length > 0 || Date.now() > deadline) return lines;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
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

suite("pty journey: a remote question in an attached client (ADR-099 D-8)", () => {
  it(
    "attach → question opens; Esc sets it aside; /venue questions → answer",
    async () => {
      const dir = mkdtempSync(join(tmpdir(), "r4-pty-remote-ask-"));
      const log = join(dir, "bridge.log");
      const tui = await boot(log);
      try {
        // A5: opening a remote session whose worker waits on the user.
        await expectScreen(tui, TITLE, 20_000);
        await expectScreen(tui, QUESTION);
        tui.send(KEYS.esc);
        await expectGone(tui, TITLE);
        // Set aside: the client does not reopen it on its own…
        await tui.pump(2_500);
        await expectGone(tui, TITLE, 500);
        // …until /venue questions asks for it.
        await submit(tui, "/venue questions");
        await expectScreen(tui, TITLE);
        await expectScreen(tui, QUESTION);
        tui.send("dataset B");
        await expectScreen(tui, "dataset B");
        tui.send(KEYS.enter);
        await expectGone(tui, TITLE);
        const sent = await answersSent(log);
        expect(sent.length).toBe(1);
        expect(sent[0]).toContain('"question_id":"q-fx-1"');
        expect(sent[0]).toContain('"answer":"dataset B"');
      } finally {
        await tui.snap("journey-remote-ask");
        await tui.kill();
        rmSync(dir, { recursive: true, force: true });
      }
    },
    JOURNEY_MS,
  );
});
