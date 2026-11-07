/**
 * Pin for the Panel chrome redesign (2026-09-29).
 * Vocabulary: solid heavy bar on the LEFT edge only; two background tones
 * (base bgPanel / raised bgHover); titles render as first-row text because
 * the native title anchored on the removed top border.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions, workspace } from "../src/stores";
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

describe("Floating chrome (no lines — bg tone separates the layer)", () => {
  it("overlays render without any border glyphs", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 140,
      height: 50,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    actions.openHelp();
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // No full-frame corners or left bars from the overlay layer.
    expect(frame).not.toContain("╭");
    expect(frame).not.toContain("╰");
    setup.renderer.destroy();
  });
});

describe("Panel chrome (left bar + two-tone bg)", () => {
  it("renders panels with left heavy bar and in-content titles", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    // ensure a detail pane is open
    actions.openPane("knowledge");
    await setup.renderOnce();
    console.log("PANE:", workspace.paneView);
    const frame = setup.captureCharFrame();
    console.log(frame);
    expect(frame).toContain("┃");
    expect(frame).toContain("Chat with R4");
    // no full-frame corner glyphs on panel borders
    expect(frame).not.toContain("╭");
    setup.renderer.destroy();
  });
});
