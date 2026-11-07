/**
 * TUI audit r2 (2026-10-17) #67, through the full App (global keymap live):
 * opening a pane emptied workspace.chatDraft while the composer kept showing
 * the text, so Ctrl+C armed EXIT instead of clearing it, and a tab switch
 * restored an empty draft.
 */

import { afterEach, beforeEach, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  resetTabsRegistryForTests();
  actions.closeAllOverlays();
});

afterEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

const exitArmed = () => workspace.chatMessages.some((m) => m.content.includes("again to exit"));

it("a pane opening mid-draft keeps the draft: Ctrl+C clears it, never arms exit", async () => {
  const setup = await testRender(() => <App />, { width: 140, height: 44 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();

  await setup.mockInput.typeText("half typed");
  await setup.renderOnce();
  expect(workspace.chatDraft).toBe("half typed");

  // e.g. a tracked UMRS workflow going running auto-opens the research pane.
  actions.openPane("research");
  await setup.renderOnce();
  expect(workspace.chatDraft).toBe("half typed");

  setup.mockInput.pressKey("c", { ctrl: true });
  await setup.renderOnce();
  expect(exitArmed()).toBe(false);
  expect(workspace.chatDraft).toBe("");
  setup.renderer.destroy();
});

it("a draft survives a pane opening and a tab round trip", async () => {
  const setup = await testRender(() => <App />, { width: 140, height: 44 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();

  const first = workspace.activeChatTabId;
  await setup.mockInput.typeText("keep me");
  await setup.renderOnce();
  actions.setSpace("knowledge");
  await setup.renderOnce();
  actions.tabCreate();
  await setup.renderOnce();
  actions.tabActivate(first);
  await setup.renderOnce();
  expect(workspace.chatDraft).toBe("keep me");
  setup.renderer.destroy();
});
