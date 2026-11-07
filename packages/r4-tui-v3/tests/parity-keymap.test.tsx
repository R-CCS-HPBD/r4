/**
 * Claude-Code parity keys are registered on the global layer with the right
 * gating (Ctrl+C works while typing; Ctrl+D only on an empty prompt), and the
 * Activity tray toggle keys (defined but never bound before) are bound.
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { getRegisteredBindings } from "../src/keymap";
import { actions, resetWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

let setup: Awaited<ReturnType<typeof testRender>> | null = null;

beforeEach(async () => {
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
  setup = await testRender(() => <App forceOffline />, { width: 160, height: 48 });
  await setup.renderOnce();
});

afterEach(() => {
  setup?.renderer.destroy();
  setup = null;
});

const binding = (key: string) => getRegisteredBindings().find((b) => b.key === key);

describe("Claude-Code parity keys", () => {
  test.each([
    ["ctrl+c", "input.interrupt"],
    ["ctrl+o", "transcript.verbose.toggle"],
    ["ctrl+t", "todos.toggle"],
    ["alt+t", "thinking.toggle"],
    ["ctrl+l", "screen.redraw"],
    ["ctrl+b", "turn.background"],
    ["f9", "activity-console.toggle"],
    ["ctrl+shift+y", "activity-console.toggle"],
  ])("%s → %s, not idle-gated", (key, cmd) => {
    const b = binding(key);
    expect(b?.cmd).toBe(cmd);
    expect(b?.idle).toBeFalsy();
  });

  test("ctrl+d exits only from an empty prompt (idle)", () => {
    const b = binding("ctrl+d");
    expect(b?.cmd).toBe("input.eof");
    expect(b?.idle).toBe(true);
  });
});
