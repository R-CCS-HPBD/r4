/**
 * Transcript vertical-spacing pins (console UX wave, 2026-09-30):
 *  1. The user bubble floats — one blank row above, TWO below — so the box
 *     never touches the LLM's reply/actions that chronologically follow it.
 *  2. The assistant header (role + @model) hugs the reply body: leading
 *     blank lines of model output never paint as a gutter under the header,
 *     and an empty (pre-token) streaming reply renders no placeholder row.
 *
 * Probe-verified with captureCharFrame before landing the fix.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { ChatPanel } from "../src/components/chat";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";
import { resetWorkspace, actions, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

function transcriptRows(frame: string): string[] {
  // Strips the chat Panel's heavy bar, then the message cell's left spine
  // (visibility wave 2026-10-19 — single-style border tinted by role), and
  // finally the scrollbox's block-element scrollbar glyphs sitting at the
  // right end of otherwise-blank rows (they paint once content overflows).
  return frame
    .split("\n")
    .map((l) =>
      l
        .replace(/\s+$/, "")
        .replace(/[▀-▟]+$/u, "")
        .replace(/\s+$/, "")
        .replace(/^┃\s?/, "")
        .replace(/^│\s?/, ""),
    );
}

describe("transcript vertical spacing", () => {
  it("user bubble floats: blank rows above AND below the box", async () => {
    setWorkspace("chatMessages", [
      { id: "a0", role: "assistant", content: "earlier reply", timestamp: 1, model: "m1" },
      { id: "u1", role: "user", content: "user question", timestamp: 2 },
      { id: "a1", role: "assistant", content: "next reply", timestamp: 3, model: "m1" },
    ] as never);
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    const rows = transcriptRows(setup.captureCharFrame());
    const youRow = rows.findIndex((l) => l.startsWith("You"));
    const userText = rows.findIndex((l) => l.startsWith("user question"));
    const nextHeader = rows.findIndex((l, i) => i > youRow && /^R4 .+@m1/.test(l));
    expect(youRow).toBeGreaterThan(0);
    expect(nextHeader).toBeGreaterThan(youRow);
    // Above the bubble: at least one blank row after the previous reply.
    expect(rows[youRow - 1]).toBe("");
    // Below the bubble: two blank rows before the next LLM section.
    expect(rows[nextHeader - 1]).toBe("");
    expect(rows[nextHeader - 2]).toBe("");
    expect(userText).toBe(youRow + 1);
    setup.renderer.destroy();
  });

  it("assistant cells carry the role spine and breathe above (visibility wave 2026-10-19)", async () => {
    setWorkspace("chatMessages", [
      { id: "u1", role: "user", content: "question", timestamp: 1 },
      { id: "a1", role: "assistant", content: "answer", timestamp: 2, model: "m1" },
      { id: "a2", role: "assistant", content: "follow-up", timestamp: 3, model: "m1" },
    ] as never);
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    const raw = setup.captureCharFrame().split("\n");
    // Header rows of both assistant cells open with panel bar + cell spine.
    const a1 = raw.findIndex((l) => /^┃ ?│ ?R4 /.test(l));
    const a2 = raw.findIndex((l, i) => i > a1 && /^┃ ?│ ?R4 /.test(l));
    expect(a1).toBeGreaterThan(0);
    expect(a2).toBeGreaterThan(a1);
    // One blank row above EVERY cell — consecutive replies never weld.
    const stripped = transcriptRows(raw.join("\n"));
    for (const i of [a1, a2]) expect(stripped[i - 1]).toBe("");
    setup.renderer.destroy();
  });

  it("header hugs content: leading newlines of the reply never paint", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content={"\n\nfirst real line\nsecond"} streaming={false} />,
      { width: 40, height: 8 },
    );
    await setup.renderOnce();
    const lines = setup.captureCharFrame().split("\n").map((l) => l.trimEnd());
    expect(lines[0]).toContain("first real line");
    expect(lines[1]).toContain("second");
    setup.renderer.destroy();
  });

  it("empty streaming reply renders no placeholder row", async () => {
    const setup = await testRender(
      () => <MarkdownBlock content="" streaming={true} />,
      { width: 40, height: 6 },
    );
    await setup.renderOnce();
    expect(setup.captureCharFrame().trim()).toBe("");
    setup.renderer.destroy();
  });
});
