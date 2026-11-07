/**
 * G — transcript copy surface: ctrl+g enters the message cursor (was: the
 * g,y comma chord, retired when its prefix swallowed typing "g").
 */

import { describe, expect, it, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { groupBindings, whichKeyRows } from "../src/lib/which-key";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

/** Mirrors the global bindings in src/keymap/index.ts. */
const BINDINGS = [
  { key: "ctrl+g", cmd: "transcript.cursor.enter" },
];

describe("transcript copy keymap exposure", () => {
  it("registers ctrl+g as the cursor entry for the which-key overlay", () => {
    const groups = groupBindings(BINDINGS);
    const transcript = groups.find((g) => g.group === "transcript");
    expect(transcript).toBeDefined();
    expect(transcript!.entries).toHaveLength(1);
    const rows = whichKeyRows(groups);
    expect(rows.some((row) => row.includes("ctrl+g → transcript.cursor.enter"))).toBe(true);
  });

  it("no chord-prefix bindings remain (prefix letters swallowed typing)", () => {
    const chordy = BINDINGS.filter((b) => b.key.includes(","));
    expect(chordy).toHaveLength(0);
    expect(BINDINGS).toHaveLength(1);
  });
});

describe("regression: comma-chord prefixes no longer eat typed letters", () => {
  it("typing 'g' as the FIRST chat character lands in the draft", async () => {
    const setup = await testRender(() => <App />, { width: 140, height: 40 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    await setup.mockInput.typeText("g");
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("g");
    setup.renderer.destroy();
  });

  it("typing 'v' as the FIRST chat character lands in the draft", async () => {
    const setup = await testRender(() => <App />, { width: 140, height: 40 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    await setup.mockInput.typeText("v");
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("v");
    setup.renderer.destroy();
  });

  it("ctrl+g enters the message cursor when the transcript has selectable rows", async () => {
    const setup = await testRender(() => <App />, { width: 140, height: 40 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    actions.sendSystemNotice("seed row for cursor entry");
    setWorkspace("chatMessages", [
      ...workspace.chatMessages,
      {
        id: "u1",
        role: "user" as const,
        content: "hello",
        timestamp: new Date().toISOString(),
      },
    ]);
    await setup.renderOnce();
    setup.mockInput.pressKey("g", { ctrl: true });
    await setup.renderOnce();
    expect(workspace.messageCursor).not.toBeNull();
    setup.renderer.destroy();
  });
});
