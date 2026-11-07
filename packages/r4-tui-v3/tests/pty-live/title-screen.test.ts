/**
 * Title screen (pty-live): the launch frame shows the current logos + hint,
 * its input owns the keyboard, the first message submits through to chat,
 * and Esc skips — without leaking keystrokes into later slash routing.
 * Run: R4_PTY_LIVE=1 R4_TUI_BACKEND=offline bun test tests/pty-live/title-screen.test.ts
 */
import { describe, expect, it } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const LIVE = process.env.R4_PTY_LIVE === "1";
const suite = LIVE ? describe : describe.skip;

suite("title screen (offline-safe)", () => {
  it("shows logos + hint; input owns keys; first message submits through to chat", async () => {
    const tui: TuiProcess = await spawnTui({ live: false });
    try {
      // 1. Title screen renders with the current branding + skip hint.
      const title = await tui.waitFor("esc to skip", 20_000);
      expect(title).toContain("Research Companion Console");
      await tui.snap("title-screen");

      // 2. Typing lands in the title input.
      tui.send("probe-draft");
      const typed = await tui.waitFor("probe-draft", 5_000);
      expect(typed).toContain("probe-draft");

      // 3. Enter submits: the title screen closes AND the message is the
      //    first chat user message.
      tui.send(KEYS.enter);
      const chat = await tui.waitFor("Chat with R4", 10_000);
      expect(chat).toContain("probe-draft");
      await tui.snap("title-screen-after-submit");
    } finally {
      tui.kill();
    }
  });

  it("slash command on the title screen routes to the dispatcher, not the LLM (the /sessions 502)", async () => {
    const tui: TuiProcess = await spawnTui({ live: false });
    try {
      await tui.waitFor("esc to skip", 20_000);
      // /endpoint works offline — same dispatch path as /sessions. Before the
      // routing fix this text went to the LLM bridge as a chat turn (502).
      tui.send("/endpoint");
      await tui.pump(600);
      tui.send(KEYS.enter);
      const frame = await tui.waitFor("work — agentic control", 10_000);
      expect(frame).toContain("work — agentic control");
      expect(frame).not.toContain("esc to skip");
      await tui.snap("title-screen-slash-endpoint");
    } finally {
      tui.kill();
    }
  });

  it("Esc skips to chat; later slash routing is unaffected (no leaked Esc)", async () => {
    const tui: TuiProcess = await spawnTui({ live: false });
    try {
      await tui.waitFor("esc to skip", 20_000);
      tui.send(KEYS.esc);
      // waitFor returns the frame on timeout (no throw) — assert the splash
      // really closed, otherwise later typing lands in the title input.
      const chat = await tui.waitFor("Chat with R4", 10_000);
      expect(chat).toContain("Chat with R4");
      expect(chat).not.toContain("esc to skip");

      // Regression pin: a stray Esc in the chat UI toggles panes and breaks
      // slash diversion (the "/endpoint" → chat "endpoint" eaten-slash bug).
      tui.send("/endpoint");
      await tui.pump(600);
      tui.send(KEYS.enter);
      const frame = await tui.waitFor("work — agentic control", 10_000);
      expect(frame).toContain("work — agentic control");
      await tui.snap("title-screen-esc-then-endpoint");
    } finally {
      tui.kill();
    }
  });
});
