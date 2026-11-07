/**
 * pty-live journey (ADR-099 D-2, maintainer decision M-4): the user chooses
 * the agent's web access when a session opens.
 *
 * The real dev TUI in a PTY against fixture-bridge/server.ts with
 * R4_PTY_FIXTURE_WEB=ask (the session carries `web_access` and opens without
 * a choice, as a fresh real bridge does). Opt-in like the rest of this tier:
 * `R4_PTY_LIVE=1 bun test tests/pty-live/journeys-web.test.ts`.
 */

import { describe, it } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 90_000;
const QUESTION = "web access for the agent in this session";

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, extraEnv: { R4_PTY_FIXTURE_WEB: "ask" } });
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

suite("pty journey: the web-access question (ADR-099 D-2)", () => {
  it(
    "first turn → web question; new local session → web question; /web → allowlist",
    async () => {
      const tui = await boot();
      try {
        // The boot session has no choice: the first turn waits for the answer.
        await submit(tui, "hello web question");
        await expectScreen(tui, QUESTION);
        await expectScreen(tui, "Shell, Python and MCP tools are not filtered locally");
        await tui.pump(200);
        tui.send(KEYS.enter); // Off is preselected
        await expectGone(tui, QUESTION);
        await expectScreen(tui, "FIXTURE-REPLY (prior turns: 0)");

        // A new local session: the venue choice, then the web question.
        await submit(tui, "/sessions new");
        await expectScreen(tui, "where should this session run?");
        await tui.pump(300);
        tui.send("l");
        await expectScreen(tui, QUESTION);
        await tui.pump(200);
        tui.send(KEYS.enter);
        await expectScreen(tui, "new session started (local venue · web off)");

        // /web shows and changes it: Allowlist with one domain.
        await submit(tui, "/web");
        await expectScreen(tui, QUESTION);
        await tui.pump(200);
        tui.send(KEYS.down);
        await expectScreen(tui, "domains:");
        tui.send("pypi.org");
        await expectScreen(tui, "domains: pypi.org");
        tui.send(KEYS.enter);
        await expectScreen(tui, "web access: allowlist (pypi.org)");
      } finally {
        await tui.snap("journey-web-access");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );
});
