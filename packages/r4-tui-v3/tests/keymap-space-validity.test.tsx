/**
 * Regression invariants from the 2026-09-02 bug-check:
 *  1. Every keymap binding's `space` value is a VALID Space (typos = silently
 *     dead verbs — surfaced only by live clicking).
 *  2. The new pane families (sessions/codeview) actually bind their verbs.
 * Needs a live keymap — the App harness provides one.
 */

import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { getRegisteredBindings } from "../src/keymap";
import { resetWorkspace, actions } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const VALID_SPACES = new Set([
  "dashboard",
  "research",
  "tasks",
  "work",
  "knowledge",
  "infrastructure",
  "settings",
  "loop",
  "sessions",
  "codeview",
  "catalog",
  "term",
]);

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  agentActions.resetAgentRuntime();
  resetWorkspace();
});

afterEach(async () => {
  stopBackendPoller();
  stopKnowledgePoller();
  await new Promise((r) => setTimeout(r, 0));
  agentActions.resetAgentRuntime();
  resetWorkspace();
});

describe("keymap binding spaces (live keymap)", () => {
  test("every space-scoped binding names a real Space", async () => {
    const setup = await testRender(() => <App />, { width: 100, height: 32 });
    await setup.renderOnce();
    const bad = getRegisteredBindings().filter(
      (b) => b.space && !VALID_SPACES.has(b.space),
    );
    setup.renderer.destroy();
    expect(bad).toEqual([]);
  });

  test("sessions and codeview verb families are bound to their spaces", async () => {
    const setup = await testRender(() => <App />, { width: 100, height: 32 });
    await setup.renderOnce();
    const bindings = getRegisteredBindings();
    const sessionsBound = bindings.filter((b) => b.space === "sessions");
    const codeviewBound = bindings.filter((b) => b.space === "codeview");
    const termBound = bindings.filter((b) => b.space === "term");
    const keyFocusSwitch = bindings.find((b) => b.cmd.startsWith("term.") || b.key === "ctrl+alt+t");
    setup.renderer.destroy();
    expect(sessionsBound.length).toBeGreaterThanOrEqual(7); // up/down/enter/return/n/r/p/d/x
    expect(codeviewBound.length).toBeGreaterThanOrEqual(6); // up/down/o/r/R/t
    // Click-only focus (2026-10-03): NO key moves terminal focus — no
    // term.* commands, no ctrl+alt+t, no term-scoped bindings.
    expect(termBound.length).toBe(0);
    expect(keyFocusSwitch).toBeUndefined();
  });
});
