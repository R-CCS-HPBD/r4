/**
 * Chat ↔ terminal click-focus regression pin (2026-10-03).
 *
 * Real PTY finding: a click in the chat column to RELEASE terminal focus
 * hands keyboard focus to the transcript ScrollBox (OpenTUI's focus-on
 * mouse-down), and typed text never reached the composer again. The
 * composer's release-edge effect must re-grab focus explicitly.
 */

import { it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions, workspace, setWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { termSession } from "../src/terminal/pty-session";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(async () => {
  termSession.kill();
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

it("typing reaches the composer after click-release from terminal focus", async () => {
  const setup = await testRender(() => <App />, { width: 140, height: 44 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();

  // /term opens the pane UNFOCUSED — typing works (baseline).
  actions.openPane("term");
  await setup.renderOnce();
  expect(workspace.paneFocus).toBe(false);
  await setup.mockInput.typeText("AAA");
  await setup.renderOnce();
  expect(workspace.chatDraft).toContain("AAA");

  // Click-to-focus (store edge — the pane's onMouseDown path).
  setWorkspace("paneFocus", true);
  await setup.renderOnce();

  // REAL click-release in the chat column: scrollbox takes keyboard focus.
  await setup.mockMouse.click(6, 6, 0);
  await setup.renderOnce();
  expect(workspace.paneFocus).toBe(false);

  await setup.mockInput.typeText("BBB");
  await setup.renderOnce();
  expect(workspace.chatDraft).toContain("BBB");
  setup.renderer.destroy();
  termSession.kill();
});
