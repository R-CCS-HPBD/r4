/**
 * Stabilization 2026-10-21 (w3-tui item 8, tiny-terminal probe): the composer
 * input stays on screen at every short height. At 30x10, after one turn, the
 * TTFT receipt row and the newline-hint row pushed the composer below the
 * status bar — the input was drawn off-screen and you typed blind. The chat
 * column now budgets its fixed rows like the splash does: the hint row and
 * the cost/TTFT receipt yield before the composer.
 */
import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { Shell } from "../src/layout/shell";
import { actions, resetWorkspace, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function frameAt(width: number, height: number): Promise<string> {
  const setup = await testRender(() => <Shell />, { width, height });
  await setup.renderOnce();
  await setup.renderOnce();
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  return frame;
}

describe("composer on short terminals", () => {
  for (const [width, height] of [[30, 10], [40, 10], [40, 11], [40, 12], [50, 13], [50, 14], [80, 16]] as const) {
    it(`${width}x${height} after a turn: the input row is on screen`, async () => {
      setWorkspace("lastTurnTtftMs", 32_600 as never);
      const frame = await frameAt(width, height);
      expect(frame).toContain("›");
    });
  }

  it("a roomy terminal keeps the hint and the TTFT receipt", async () => {
    setWorkspace("lastTurnTtftMs", 32_600 as never);
    const frame = await frameAt(100, 30);
    expect(frame).toContain("ttft");
    expect(frame).toContain("newline");
    expect(frame).toContain("›");
  });
});
