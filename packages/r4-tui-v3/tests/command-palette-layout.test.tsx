/**
 * Command palette layout regression pin.
 *
 * Bug (report 2026-09-12): ROW_CAP (19) + chrome rows exceeded the outer
 * box's maxHeight (24) — Yoga clipped via overflow="hidden", so the footer
 * (scroll grammar / range line) and the last list rows rendered OUTSIDE the
 * visible card with no margin to the border. Height budget is now
 * self-consistent (ROW_CAP = MAX - CHROME). Pin: footer always visible.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 200));
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("Command palette layout", () => {
  it("keeps the footer (range + scroll grammar) inside the visible card", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openCommandPalette();
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("Type a command");
    // Footer line must be on screen — pre-fix it was clipped below the card.
    expect(frame).toContain("↑↓ scroll");
    expect(frame).toContain("Esc close");
    // Range prefix (e.g. "1–17 of N") must be visible too.
    expect(/1–\d+ of \d+/.test(frame)).toBe(true);

    setup.renderer.destroy();
  });

  it("footer stays visible while paging down through the list", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openCommandPalette();
    await setup.renderOnce();

    // Move the selection down past the initial window — the list scrolls;
    // border + footer must remain visible.
    for (let i = 0; i < 40; i++) {
      setup.mockInput.pressArrow("down");
      await setup.renderOnce();
    }
    const frame = setup.captureCharFrame();
    expect(frame).toContain("↑↓ scroll");
    expect(/\d+–\d+ of \d+/.test(frame)).toBe(true);

    setup.renderer.destroy();
  });
});
