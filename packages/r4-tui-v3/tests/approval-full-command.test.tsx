/**
 * Approval card shows the WHOLE command it approves (stabilization sweep
 * 2026-10-21, chat#1). The card kept the first 2 lines of the first 300
 * chars and cut each line at the column with "…", so pressing y ran a third
 * line / a long-line tail that was never on screen. Pins: every line and
 * every tail renders; past the row budget the card scrolls and says how
 * many rows are below; control characters are shown, never interpreted.
 */

import { afterEach, describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions } from "../src/stores";
import { ToolApprovalOverlay } from "../src/components/tool-approval-overlay";
import { buildApprovalPreview, wrapApprovalRows } from "../src/lib/approval-preview";
import { resetTermWidthForTest, syncChatWidth } from "../src/lib/term-width";

afterEach(() => {
  actions.closeToolApproval();
  resetTermWidthForTest();
});

async function renderCard(command: string, width = 100, height = 50) {
  syncChatWidth(width);
  actions.openToolApproval({
    id: 77,
    tool: "shell_exec",
    description: `Run shell command: ${command}`,
    sessionApprovalAllowed: true,
  });
  const setup = await testRender(() => <ToolApprovalOverlay />, { width, height });
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  return frame;
}

describe("approval card shows the whole command", () => {
  test("a third line is on the card", async () => {
    const frame = await renderCard("echo step-one\necho step-two\ntouch /tmp/HIDDEN_THIRD");
    expect(frame).toContain("echo step-one");
    expect(frame).toContain("echo step-two");
    expect(frame).toContain("touch /tmp/HIDDEN_THIRD");
  });

  test("the tail of a long line is on the card", async () => {
    const long = `echo ${"padding-".repeat(30)}; touch /tmp/HIDDEN_TAIL`;
    const frame = await renderCard(long);
    expect(frame.replace(/\s+/g, "")).toContain("touch/tmp/HIDDEN_TAIL");
    expect(frame).not.toContain("…");
  });

  test("past the row budget the card scrolls and marks the rows below", async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `echo line-${i}`).join("\n");
    const frame = await renderCard(lines);
    expect(frame).toContain("echo line-0");
    expect(frame).toContain("30 rows, 12 visible — scroll here");
    expect(frame).not.toContain("echo line-12");
  });

  test("control characters are shown as escapes, never interpreted", () => {
    const rows = buildApprovalPreview({
      tool: "shell_exec",
      description: "Run shell command: echo safe\rrm -rf ~ \u001b[2K",
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.text).toBe("echo safe\\u{d}rm -rf ~ \\u{1b}[2K");
  });

  test("wide glyphs wrap by terminal cells", () => {
    const pieces = wrapApprovalRows([{ label: "$", text: "日本語".repeat(10) }], 10);
    expect(pieces.map((p) => p.text)).toEqual(["日本語日本", "語日本語日", "本語日本語", "日本語日本", "語日本語日", "本語日本語"]);
    expect(pieces[0]!.label).toBe("$");
    expect(pieces[1]!.label).toBe("");
  });
});
