/**
 * c2-tui (2026-10-21, PTY journey j6): the transcript cursor (Ctrl+↑ /
 * Shift+Tab, then ↑↓) moved its selection to messages scrolled out of view
 * and the transcript stayed put — the ▸ marker vanished and `c`/`r`/`e`/`f`
 * acted on a message the user could not see. The transcript now scrolls the
 * selected message into view.
 */
import { describe, expect, it, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setWorkspace } from "../src/stores";
import { ChatPanel } from "../src/components/chat";

function seed(count: number): void {
  setWorkspace(
    "chatMessages",
    Array.from({ length: count }, (_, i) => ({
      id: `m${i}`,
      role: i % 2 === 0 ? ("user" as const) : ("assistant" as const),
      content: `message-${i}-body`,
      timestamp: "2026-10-21T10:00:00Z",
    })),
  );
}

async function settle(setup: Awaited<ReturnType<typeof testRender>>): Promise<string> {
  for (let i = 0; i < 4; i++) await setup.renderOnce();
  return setup.captureCharFrame();
}

describe("transcript cursor keeps its message on screen", () => {
  beforeEach(() => resetWorkspace());

  it("moving the cursor up past the viewport scrolls the selected message into view", async () => {
    seed(40);
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    const bottom = await settle(setup);
    expect(bottom).toContain("message-39-body");
    expect(bottom).not.toContain("message-10-body");

    actions.enterMessageCursor();
    actions.moveMessageCursor(-29);
    const moved = await settle(setup);
    expect(moved).toContain("message-10-body");
    expect(moved).toContain("▸");

    actions.moveMessageCursor(25);
    const back = await settle(setup);
    expect(back).toContain("message-35-body");
    expect(back).toContain("▸");
    setup.renderer.destroy();
  });
});

// c2-tui (2026-10-21, PTY journey j6): in a long session the transcript
// could only be scrolled with the mouse wheel — PgUp/PgDn in the composer
// did nothing, so a keyboard-only user (or one who turned mouse reporting
// off for native selection) could not read back. They page the transcript.
describe("PgUp / PgDn page the transcript from the composer", () => {
  beforeEach(() => resetWorkspace());

  it("PgUp shows older messages, PgDn returns to the newest", async () => {
    seed(40);
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    const bottom = await settle(setup);
    expect(bottom).toContain("message-39-body");
    for (let i = 0; i < 3; i++) setup.mockInput.pressKey("\x1b[5~");
    const up = await settle(setup);
    expect(up).not.toContain("message-39-body");
    const shown = [...up.matchAll(/message-(\d+)-body/g)].map((m) => Number(m[1]));
    expect(Math.min(...shown)).toBeLessThan(36);
    for (let i = 0; i < 6; i++) setup.mockInput.pressKey("\x1b[6~");
    const down = await settle(setup);
    expect(down).toContain("message-39-body");
    setup.renderer.destroy();
  });
});
