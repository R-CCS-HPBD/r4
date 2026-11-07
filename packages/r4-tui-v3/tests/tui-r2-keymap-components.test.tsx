/**
 * TUI audit 2026-10-17 round 2 (keymap lane) — component-level key owners:
 * the Settings Skills list had two focused KeyTraps (#15), and chat cells
 * captured their list index once, so the message cursor landed on the wrong
 * row after a mid-list removal (#69).
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { ChatPanel } from "../src/components/chat";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { ChatMessage, UiSkill } from "../src/types";

type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  reset();
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 40): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

function skill(name: string, source: string): UiSkill {
  return { name, source, hasMcp: false, description: "" };
}

describe("Settings Skills list has one key owner (#15)", () => {
  it("↓ + Enter selects the row the grouped list shows, through the full App keymap", async () => {
    // Pin the inventory: a bridge snapshot or the offline poll merges the
    // repo's builtin skills into the list at an unpredictable moment, and
    // one landing mid-test put "academic-plotting" under the cursor (a race
    // the extra Tab below widened, 2026-10-27).
    const realSetBackendState = actions.setBackendState;
    const realSetSkillInventory = actions.setSkillInventory;
    const patched = actions as Record<string, unknown>;
    patched.setBackendState = (state: Parameters<typeof realSetBackendState>[0]) =>
      realSetBackendState({ ...state, skills: undefined });
    patched.setSkillInventory = () => {};
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    try {
      await setup.renderOnce();
      actions.closeAllOverlays();
      await settle(setup, 60);
      // Display order groups by source: remote: zeta, beta / local: alpha.
      setWorkspace("skills", [skill("zeta", "remote"), skill("alpha", "local"), skill("beta", "remote")]);
      actions.openPane("settings");
      setWorkspace("paneFocus", true);
      await settle(setup, 80);
      // Preferences has the keys first since issue #217 (2026-10-27); Tab
      // hands them to the Skills list.
      setup.mockInput.pressTab();
      await settle(setup);
      // Row 0 is the "remote" header; one ↓ lands on zeta.
      setup.mockInput.pressArrow("down");
      await settle(setup);
      setup.mockInput.pressEnter();
      await settle(setup);
      const notices = workspace.chatMessages.map((m) => m.content).join("\n");
      expect(notices).toContain("skill zeta");
      expect(notices).not.toContain("skill alpha");
    } finally {
      setup.renderer.destroy();
      patched.setBackendState = realSetBackendState;
      patched.setSkillInventory = realSetSkillInventory;
    }
  });
});

describe("chat cell cursor survives a mid-list removal (#69)", () => {
  it("exactly the cursor row shows the marker after a queued row is taken back", async () => {
    const at = new Date().toISOString();
    const rows: ChatMessage[] = [
      { id: "u1", role: "user", content: "first prompt", timestamp: at },
      { id: "q1", role: "user", content: "queued prompt", timestamp: at, queued: true },
      { id: "a1", role: "assistant", content: "LAST-REPLY", timestamp: at },
    ];
    setWorkspace("chatMessages", rows);
    const setup = await testRender(() => <ChatPanel />, { width: 140, height: 40 });
    await settle(setup);
    setWorkspace("chatMessages", (list) => list.filter((m) => m.id !== "q1"));
    await settle(setup);
    setWorkspace("messageCursor", 1);
    await settle(setup);
    const frame = setup.captureCharFrame();
    expect(frame.split("\n").filter((l) => l.includes("▸")).length).toBe(1);
    expect(frame).toContain("c copy · Esc back");
    setup.renderer.destroy();
  });
});
