/**
 * App tests.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

async function teardownHarness() {
  stopBackendPoller();
  stopKnowledgePoller();
  await new Promise((resolve) => setTimeout(resolve, 0));
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 150));
  stopBackendPoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("App", () => {
  it("renders the console shell", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // V4 chat-centric shell: chat primary, status line, no nav rail.
    expect(frame).toContain("Chat with R4");
    expect(frame).toContain("Message R4");
    expect(frame).not.toContain("Command Center");
    await teardownHarness();
    setup.renderer.destroy();
  });

  it("shows the dashboard by default", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Chat with R4");
    await teardownHarness();
    setup.renderer.destroy();
  });

  it("help routes into the unified command surface", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    // S2: the legacy help overlay is gone — /help opens the palette.
    actions.openCommandPalette("/h");
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("health");
    actions.closeAllOverlays();
    await teardownHarness();
    setup.renderer.destroy();
  });

  it("opens command palette", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    actions.openCommandPalette();
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Type a command");
    actions.closeAllOverlays();
    await teardownHarness();
    setup.renderer.destroy();
  });

  it("opens slash via the unified command surface", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    actions.openCommandPalette("/he");
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // S2 single surface: "/" opens the palette preseeded; matching verbs listed.
    expect(frame).toContain("health");
    actions.closeAllOverlays();
    await teardownHarness();
    setup.renderer.destroy();
  });
});
