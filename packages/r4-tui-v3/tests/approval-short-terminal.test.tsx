/**
 * c3-tui 2026-10-21 (item 5): the approval card on a short terminal keeps
 * its key legend on screen and scrolls the command.
 *
 * On the base the card never shrank and the chat panel clipped it from the
 * bottom: at 12 rows only the first four lines of an 18-line command showed,
 * the key legend, the [Y]es/[N]o rows and the composer were gone, and
 * nothing on screen said which key answers (PTY repro
 * $C3/tui/o5-approval-short.ts, fixture turn "fx approve long").
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { ChatPanel } from "../src/components/chat";
import { approvalCardRows, approvalNeedsTight } from "../src/lib/approval-fit";
import { resetWorkspace, actions } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const COMMAND = Array.from({ length: 18 }, (_, i) => `echo long-cmd-line-${i + 1}`).join("\n");

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function frameWithApproval(rows: number): Promise<string> {
  actions.openToolApproval({
    id: 77,
    tool: "shell_exec",
    description: `Run shell command: ${COMMAND}`,
    sessionApprovalAllowed: true,
  });
  const setup = await testRender(() => <ChatPanel />, { width: 100, height: rows });
  for (let i = 0; i < 4; i++) await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  actions.closeToolApproval();
  return frame;
}

describe("approval card height budget", () => {
  // ChatPanel alone at 10 rows = a 12-row terminal (tab strip + status bar).
  it("12 rows: the legend is pinned under the first command rows", async () => {
    const frame = await frameWithApproval(10);
    expect(frame).toContain("Tool approval required");
    expect(frame).toContain("long-cmd-line-1");
    expect(frame).toContain("y/Enter once");
    expect(frame).toContain("scroll 18 rows");
  });

  it("a roomy terminal keeps the full card: legend, option rows and composer", async () => {
    const frame = await frameWithApproval(60);
    expect(frame).toContain("Keys: y/Enter allow once");
    expect(frame).toContain("[Y]es");
    expect(frame).toContain("[N]o — reject");
    expect(frame).toContain("›");
  });

  it("the tight layout starts where the natural card stops fitting", () => {
    const rows = approvalCardRows({
      extraRows: 0,
      previewRows: 1,
      previewCap: 12,
      diffRows: 0,
      optionCount: 2, // once + reject: [Y] and [N]
    });
    expect(rows).toBe(10);
    expect(approvalNeedsTight(40, rows)).toBe(false);
    expect(approvalNeedsTight(25, rows)).toBe(false);
    expect(approvalNeedsTight(24, rows)).toBe(true);
    expect(approvalNeedsTight(12, rows)).toBe(true);
  });
});
