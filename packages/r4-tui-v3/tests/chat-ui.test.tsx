/**
 * Chat UI integration test.
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
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("Chat UI", () => {
  it("accepts a typed message and shows a reply", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    await setup.mockInput.typeText("hello r4");
    await setup.renderOnce();

    // Press Enter to send.
    setup.mockInput.pressEnter();
    await setup.renderOnce();

    // Wait for the assistant reply timeout.
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("hello r4");
    expect(frame).toContain("[Offline]");
    setup.renderer.destroy();
    stopBackendPoller();
    stopKnowledgePoller();
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });
});
