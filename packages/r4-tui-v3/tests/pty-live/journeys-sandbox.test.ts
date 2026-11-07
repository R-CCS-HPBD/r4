/**
 * pty-live journey (ADR-101, local tool sandbox): off by default, and the
 * console says so when a local session opens — with how to turn it on.
 * Maintainer requirement: "default -> off as claude code but r4 tui needs to
 * notify users and how to make this on during a session at first".
 *
 * The real dev TUI in a PTY against fixture-bridge/server.ts with
 * R4_PTY_FIXTURE_SANDBOX=off (the session carries `sandbox`, off, bwrap
 * available, as a fresh real local bridge does). Opt-in like the rest of
 * this tier: `R4_PTY_LIVE=1 bun test tests/pty-live/journeys-sandbox.test.ts`.
 */

import { describe, it } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 90_000;
const NOTICE = "Sandbox off: shell, Python and MCP servers run with your full permissions";

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, extraEnv: { R4_PTY_FIXTURE_SANDBOX: "off" } });
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

suite("pty journey: the local tool sandbox notice and /sandbox (ADR-101)", () => {
  it(
    "boot → off notice + sbx:off → /sandbox on → sbx:on and the same row turns ✓",
    async () => {
      const tui = await boot();
      try {
        await expectScreen(tui, NOTICE);
        await expectScreen(tui, "/sandbox on");
        await expectScreen(tui, "sbx:off");

        await submit(tui, "/sandbox on");
        await expectScreen(tui, "sbx:on");
        await expectScreen(tui, "✓ Sandbox on (bwrap)");
        // updated in place: the off line is gone, not left above the new one
        await expectGone(tui, NOTICE);
        await expectScreen(tui, "sandbox on (bwrap) — applies from the agent's next command");
      } finally {
        await tui.snap("journey-sandbox");
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );
});
