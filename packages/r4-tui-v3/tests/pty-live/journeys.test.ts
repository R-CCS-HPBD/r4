/**
 * pty-live offline journeys (plan H-9): the real dev TUI in a real PTY,
 * CLI mode, with fixture-bridge/server.ts at the far end of the companion
 * pipe (canned turns: fixture-bridge/turns.ts). No backend, no r4, no
 * model, no user config — CI-safe.
 *
 * Off by default like the rest of this tier: R4_PTY_LIVE=1 runs them
 * (`bun run test:pty`). Every assertion reads rendered screen text; every
 * step waits on a condition (expectScreen / expectGone / expectMatch). A bare
 * pump is used only after a lone ESC (README rule 3), to let a focus change
 * settle before typing, or as the window of a "nothing more arrives" check.
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;

const CTRL_N = "\x0e";
const ALT_LEFT = "\x1b[1;3D";
const F12 = "\x1b[24~";
const TAB = "\t";
/** The tab strip (first row) lists a second tab: " 2:<title>". */
const SECOND_TAB = /^[^\n]* 2:/;
const PASTE_START = "\x1b[200~";
const PASTE_END = "\x1b[201~";
const JOURNEY_MS = 90_000;

interface JourneyOptions {
  cols?: number;
  /** Extra env for the TUI (and so the fixture bridge it spawns). */
  env?: Record<string, string>;
}

async function boot(opts: JourneyOptions): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, cols: opts.cols, extraEnv: opts.env });
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) tui.send(KEYS.esc);
  await tui.waitFor("Chat with R4", 20_000);
  // The fixture bridge greets with its scratch workspace once initialize lands.
  await tui.waitFor("workspace ready", 20_000);
  return tui;
}

/** Wait for `text`; fail with the frame when it never shows. */
async function expectScreen(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const frame = await tui.waitFor(text, timeoutMs);
  if (!frame.includes(text)) throw new Error(`"${text}" never rendered; last frame:\n${frame}`);
  return frame;
}

/** Wait for a frame matching `pattern`; fail with the frame when none does. */
async function expectMatch(tui: TuiProcess, pattern: RegExp, timeoutMs = 10_000): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = tui.screen.dump();
  while (!pattern.test(frame) && Date.now() < deadline) {
    await tui.pump(120);
    frame = tui.screen.dump();
  }
  if (!pattern.test(frame)) throw new Error(`${pattern} never rendered; last frame:\n${frame}`);
  return frame;
}

/** Poll until `text` is gone; fail with the frame when it stays. */
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

/** Lone ESC bytes are held as sequence starts: pump after each (README rule 3). */
async function esc(tui: TuiProcess): Promise<void> {
  tui.send(KEYS.esc);
  await tui.pump(250);
}

function count(frame: string, text: string): number {
  return frame.split(text).length - 1;
}

/** First screen row: the chat tab strip. */
function tabStrip(frame: string): string {
  return frame.split("\n")[0] ?? "";
}

/** Everything below the tab strip (tab titles repeat the first prompt). */
function body(frame: string): string {
  return frame.split("\n").slice(1).join("\n");
}

/** Ask the fixture for a shell_exec approval and check the card. */
async function openApproval(tui: TuiProcess): Promise<void> {
  await submit(tui, "fx approve");
  await expectScreen(tui, "Tool approval required");
  const card = await expectScreen(tui, "n reject with feedback"); // ADR-104 legend
  expect(card).toContain("$       echo fixture-approval-probe");
  expect(card).toContain("awaiting approval: shell_exec");
}

/** n → feedback stage → type → Enter. The text is proven by what Enter
 * produces: the notice and the composer draft (the clipped-card and lost-draft
 * defects were fixed 2026-10-18). */
async function rejectWithFeedback(tui: TuiProcess, feedback: string): Promise<void> {
  tui.send("n");
  await expectScreen(tui, "Reject with feedback");
  await tui.pump(300);
  tui.send(feedback);
  await tui.pump(300);
  tui.send(KEYS.enter);
}

async function journey(
  name: string,
  steps: (tui: TuiProcess) => Promise<void>,
  opts: JourneyOptions = {},
): Promise<void> {
  const tui = await boot(opts);
  try {
    await steps(tui);
  } finally {
    await tui.snap(`journey-${name}`);
    await tui.kill();
  }
}

suite("pty journeys (offline fixture bridge)", () => {
  it("compose → rewind → clear: the companion history follows the transcript", async () => {
    await journey("compose-clear-rewind", async (tui) => {
      await submit(tui, "alpha first");
      await expectScreen(tui, "FIXTURE-REPLY (prior turns: 0)");
      await submit(tui, "beta second");
      await expectScreen(tui, "FIXTURE-REPLY (prior turns: 1)");

      // Esc Esc on an empty prompt opens the rewind menu, newest first.
      await esc(tui);
      await esc(tui);
      await expectScreen(tui, "Rewind to an earlier prompt (2)");
      tui.send(KEYS.down);
      await expectScreen(tui, "▸ #");
      await tui.pump(200);
      tui.send(KEYS.enter);
      await expectScreen(tui, "rewound — the prompt is back in the input");
      tui.send(KEYS.enter);
      // session.truncate cut the bridge's history at "alpha first": its
      // resend sees no earlier turn, and "beta second" left the transcript.
      const rewound = await expectGone(tui, "beta second");
      await expectScreen(tui, "FIXTURE-REPLY (prior turns: 0)");
      expect(rewound).not.toContain("FIXTURE-REPLY (prior turns: 1)");

      await submit(tui, "/clear");
      const deadline = Date.now() + 10_000;
      while (body(tui.screen.dump()).includes("alpha first") && Date.now() < deadline) await tui.pump(120);
      expect(body(tui.screen.dump())).not.toContain("alpha first");
      await submit(tui, "gamma third");
      const cleared = await expectScreen(tui, "FIXTURE-REPLY (prior turns: 0)");
      expect(cleared).toContain("gamma third");
      expect(cleared).not.toContain("the companion kept the conversation");
    });
  }, JOURNEY_MS);

  it("approval card: n opens feedback, Enter rejects with it; y allows", async () => {
    await journey("approval-feedback", async (tui) => {
      await openApproval(tui);
      await rejectWithFeedback(tui, "try listing instead");
      const rejected = await expectScreen(tui, "the tool call was rejected");
      // The TUI's own record of the decision it sent, then the notice.
      expect(rejected).toContain("gate rejected · shell_exec");
      expect(rejected).toContain('"decision":"reject"');
      expect(rejected).toContain("rejected — your feedback is queued as your next message");
      expect(rejected).toContain("APPROVAL-TURN-END");
      await expectGone(tui, "Tool approval required");
      // The feedback is queued behind the running turn and sent when it ends
      // (stabilization sweep 2026-10-21, chat#8: it only sat in the composer).
      await expectScreen(tui, "FIXTURE-REPLY");

      await submit(tui, "fx approve again");
      await expectScreen(tui, "Tool approval required");
      tui.send("y");
      const allowed = await expectScreen(tui, "Tool ran.");
      expect(allowed).toContain("gate approved (once) · shell_exec"); // ADR-104: no auto-answer, no ask-once notice
      await expectGone(tui, "Tool approval required");
    });
  }, JOURNEY_MS);

  // Known defects, pinned with it.failing (escalated 2026-10-18): each passes
  // while the defect is there and turns red once it is fixed — then flip it
  // to it(...). Only the defect assertions (marked) may fail; every earlier
  // step is shared with a passing journey above. Checked 2026-10-18 by
  // running them as it(...): each failed on its marked assertion.
  it("approval card: the option rows and the feedback input are visible (fixed 2026-10-18)", async () => {
    await journey("approval-clip", async (tui) => {
      await openApproval(tui);
      await expectScreen(tui, "[N]o — reject", 3_000); // defect
      tui.send("n");
      await expectScreen(tui, "Tell R4 what to do instead", 3_000); // defect
    });
  }, JOURNEY_MS);

  it("approval card: the rejection feedback is sent as the next message (2026-10-21)", async () => {
    await journey("approval-feedback-draft", async (tui) => {
      await openApproval(tui);
      await rejectWithFeedback(tui, "try listing instead");
      await expectScreen(tui, "rejected — your feedback is queued as your next message");
      await expectGone(tui, "Tool approval required");
      // chat#8: "Feedback queued" used to park the text in the composer only.
      await expectScreen(tui, "FIXTURE-REPLY");
    });
  }, JOURNEY_MS);

  it("plan card: n keeps planning, a executes the plan into the transcript", async () => {
    await journey("plan-card", async (tui) => {
      // A plan card only exists in plan mode (session.confirm_plan).
      await submit(tui, "/mode plan");
      await expectScreen(tui, "mode:plan");
      await submit(tui, "fx plan");
      const card = await expectScreen(tui, "Plan awaiting confirmation");
      expect(card).toContain("PLAN-PROPOSED");
      expect(card).toContain("[a] accept — switch to autopilot and run");
      tui.send("n");
      await expectGone(tui, "Plan awaiting confirmation");

      // The next message revises the pending plan; its card returns.
      await submit(tui, "fx plan two");
      await expectScreen(tui, "Plan awaiting confirmation");
      tui.send("a");
      const ran = await expectScreen(tui, "Executing the fixture plan (execute_auto)");
      await expectGone(tui, "Plan awaiting confirmation");
      // ADR-104 D-1: execute_auto switched the session to autopilot; the badge follows the snapshot.
      expect(await expectScreen(tui, "mode:autopilot")).not.toContain("perm:");
      expect(ran).toContain("fx plan two");
    });
  }, JOURNEY_MS);

  it("tab switch during a turn: the stream stays in its own tab", async () => {
    await journey("tab-switch-mid-turn", async (tui) => {
      await submit(tui, "fx slow");
      await expectScreen(tui, "slow-part-1");
      tui.send(CTRL_N);
      const fresh = await expectMatch(tui, SECOND_TAB);
      expect(tabStrip(fresh)).toContain("●");
      await tui.pump(1_500);
      const other = tui.screen.dump();
      expect(other).not.toContain("slow-part");
      tui.send(ALT_LEFT);
      const back = await expectScreen(tui, "SLOW-TURN-DONE", 15_000);
      expect(back).toContain("slow-part-1");
      expect(count(back, "SLOW-TURN-DONE")).toBe(1);
    });
  }, JOURNEY_MS);

  it("background tab finish: the busy glyph settles without a visit", async () => {
    await journey("background-tab-finish", async (tui) => {
      await submit(tui, "fx slow");
      await expectScreen(tui, "slow-part-1");
      tui.send(CTRL_N);
      const fresh = await expectMatch(tui, SECOND_TAB);
      expect(tabStrip(fresh)).toContain("●");
      // The turn ends while tab 1 is in the background (~6 s of chunks).
      const deadline = Date.now() + 20_000;
      while (tabStrip(tui.screen.dump()).includes("●") && Date.now() < deadline) await tui.pump(200);
      const settled = tui.screen.dump();
      expect(tabStrip(settled)).not.toContain("●");
      expect(settled).not.toContain("SLOW-TURN-DONE");
      tui.send(ALT_LEFT);
      const back = await expectScreen(tui, "SLOW-TURN-DONE");
      expect(count(back, "SLOW-TURN-DONE")).toBe(1);
    });
  }, JOURNEY_MS);

  it("slash forward with args: typed and pasted lines reach the companion intact", async () => {
    await journey("slash-forward-args", async (tui) => {
      // Typed: a bare-local-args-forward row (manifest) — the args form goes
      // to dispatch_slash_command verbatim; the fixture echoes what it got.
      await submit(tui, "/skills on fx-skill-probe");
      await expectScreen(tui, "fixture companion ran: /skills on fx-skill-probe");
      // Pasted (bracketed paste): a companion-only row with an argument.
      tui.send(`${PASTE_START}/export fx-notes.md${PASTE_END}`);
      await expectScreen(tui, "/export fx-notes.md");
      await tui.pump(150);
      tui.send(KEYS.enter);
      const ran = await expectScreen(tui, "fixture companion ran: /export fx-notes.md");
      expect(ran).not.toContain("Unknown slash command");
    });
  }, JOURNEY_MS);

  // Typed (not pasted), "/export fx-notes.md" dead-ended in the command
  // palette ("No commands match.") until 2026-10-18: the palette resolved
  // against localSlashCommands() only; it now uses the merged catalog.
  it("slash forward: a typed companion-only command with args (fixed 2026-10-18)", async () => {
    await journey("slash-typed-companion-only", async (tui) => {
      tui.send("/export fx-notes.md");
      await expectScreen(tui, "export fx-notes.md", 5_000);
      await tui.pump(300);
      tui.send(KEYS.enter);
      await expectScreen(tui, "fixture companion ran: /export fx-notes.md", 3_000);
    });
  }, JOURNEY_MS);

  it("Esc ladder: stop the turn, close the overlay, clear the draft, then rewind", async () => {
    await journey("esc-ladder", async (tui) => {
      await submit(tui, "fx slow");
      await expectScreen(tui, "slow-part-1");
      // Rung 1 (2026-11-03): one Esc only arms — the turn keeps streaming and
      // the bar says how to stop it; the second Esc within 1 s stops it, the
      // composer is idle again and no further chunk lands (the fixture would
      // stream 8 parts over ~6 s).
      await esc(tui);
      await expectScreen(tui, "press Esc again to stop", 2_000);
      await esc(tui);
      const stopped = await expectGone(tui, "Esc Esc to stop");
      await tui.pump(2_000);
      const later = tui.screen.dump();
      expect(count(later, "slow-part-")).toBe(count(stopped, "slow-part-"));
      expect(later).not.toContain("SLOW-TURN-DONE");

      tui.send("draftprobe");
      await expectScreen(tui, "draftprobe");
      tui.send(F12);
      await expectScreen(tui, "help — F12 opens it");
      await esc(tui);
      const closed = await expectGone(tui, "help — F12 opens it");
      expect(closed).toContain("draftprobe");
      await esc(tui);
      await expectGone(tui, "draftprobe");
      await esc(tui);
      await esc(tui);
      await expectScreen(tui, "Rewind to an earlier prompt");
      await esc(tui);
      await expectGone(tui, "Rewind to an earlier prompt");
      tui.send("focusprobe");
      await expectScreen(tui, "focusprobe");
    });
  }, JOURNEY_MS);

  it("infrastructure Tab: focuses the pane, then switches Endpoints ↔ Jobs", async () => {
    // 200 columns: at 140 the detail pane is too narrow for its list rows.
    await journey("infra-tab", async (tui) => {
      await submit(tui, "/infra");
      await expectScreen(tui, "Infrastructure · Tab to focus");
      await expectScreen(tui, "fx-endpoint-", 15_000);
      await expectScreen(tui, "fx-train-job", 15_000);
      tui.send(TAB);
      const focused = await expectGone(tui, "Infrastructure · Tab to focus");
      expect(focused).toContain("▸ Endpoints");
      tui.send(TAB);
      const jobs = await expectScreen(tui, "▸ Jobs");
      expect(jobs).toContain("Job · running · [c] cancel");
      expect(jobs).toMatch(/▸ \S+ fx-train-job/);
      tui.send(TAB);
      const endpoints = await expectScreen(tui, "▸ Endpoints");
      expect(endpoints).toContain("Endpoint · ready");
      expect(endpoints).toMatch(/▸ \S+ fx-endpoint-/);
    }, { cols: 200 });
  }, JOURNEY_MS);

  it("remote watcher: a frontend session's queued turn reports its tool activity and lands once", async () => {
    await journey(
      "remote-watcher",
      async (tui) => {
        await submit(tui, "fx remote");
        const done = await expectScreen(tui, "REMOTE-TURN-DONE", 20_000);
        expect(count(done, "REMOTE-TURN-DONE")).toBe(1);
        // The summary counts the tool activity the running polls reported.
        await expectScreen(tui, "remote turn 1 — 1 tool action");
        await tui.pump(1_500);
        expect(count(tui.screen.dump(), "REMOTE-TURN-DONE")).toBe(1);
      },
      { env: { R4_PTY_FIXTURE_VENUE: "frontend" } },
    );
  }, JOURNEY_MS);

  it("thought block and citations render for a reasoning turn", async () => {
    await journey("thought-citations", async (tui) => {
      await submit(tui, "fx think");
      await expectScreen(tui, "Thought: weighing-fixture-hypotheses");
      const done = await expectScreen(tui, "THOUGHT-TURN-DONE [S1]");
      await expectScreen(tui, "Citations:");
      const cited = await expectScreen(tui, "[S1] Fixture Source Paper");
      expect(cited).toContain("[passage/fx-artif]");
      expect(done).toContain("THOUGHT-TURN-DONE");
    });
  }, JOURNEY_MS);
});
