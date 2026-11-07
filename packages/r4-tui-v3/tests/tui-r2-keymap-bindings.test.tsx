/**
 * TUI audit 2026-10-17 round 2 (keymap lane) — global bindings that shadowed
 * focused owners, driven through the FULL <App/> so the keymap is in play:
 * uppercase R/O/T bindings (#2/#12), the tool-approval card (#3), activity
 * digits (#4), Ctrl+N over overlays (#5), research Tab (#6), team-overlay
 * Enter (#7), the palette terminal label (#10/#68), knowledge proposal keys
 * (#16).
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { teamsActions } from "../src/stores/teams";
import { scienceActions, scienceStore } from "../src/stores/science";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { getRegisteredBindings } from "../src/keymap";
import { getCommands } from "../src/commands";

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
  setCompanionBridgeForTests(null);
  scienceActions.reset();
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

async function mountApp(): Promise<Setup> {
  const setup = await testRender(() => <App />, { width: 160, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await settle(setup);
  return setup;
}

/** Swap one teamsActions verb for a recorder; returns the call log + restore. */
function spyTeams(verb: "run" | "resume" | "report" | "openAgentTab") {
  const calls: unknown[][] = [];
  const original = teamsActions[verb];
  (teamsActions as Record<string, unknown>)[verb] = async (...args: unknown[]) => {
    calls.push(args);
  };
  return { calls, restore: () => ((teamsActions as Record<string, unknown>)[verb] = original) };
}

describe("shifted letter bindings (#2/#12)", () => {
  it("no binding is a bare uppercase letter (the parser lowercases it)", async () => {
    const setup = await mountApp(); // registers the live keymap
    const keys = getRegisteredBindings().map((b) => b.key);
    expect(keys).toContain("shift+r");
    expect(keys.filter((k) => /^[A-Z]$/.test(k))).toEqual([]);
    setup.renderer.destroy();
  });

  it("Shift+R in the team overlay resumes; plain r still runs", async () => {
    const resume = spyTeams("resume");
    const run = spyTeams("run");
    try {
      const setup = await mountApp();
      actions.setTeamDetailId("team-1");
      await settle(setup);
      setup.mockInput.pressKey("R");
      await settle(setup);
      expect(resume.calls).toEqual([["team-1"]]);
      expect(run.calls).toEqual([]);
      setup.mockInput.pressKey("r");
      await settle(setup);
      expect(run.calls).toEqual([["team-1"]]);
      expect(workspace.chatDraft).toBe("");
      setup.renderer.destroy();
    } finally {
      resume.restore();
      run.restore();
    }
  });
});

describe("tool-approval card owns its keys (#3)", () => {
  function fakeApprovalBridge() {
    const decisions: unknown[] = [];
    setCompanionBridgeForTests({
      ensureStarted: async () => {},
      shutdown: async () => {},
      request: async () => ({}),
      respondApproval: (id: unknown, decision: unknown) => decisions.push([id, decision]),
    } as never);
    return decisions;
  }

  it("n opens feedback; typed y/a stay text; Enter rejects with the feedback", async () => {
    const decisions = fakeApprovalBridge();
    const setup = await mountApp();
    setWorkspace("toolApproval", { id: "t1", tool: "bash", description: "ls", options: ["once", "always", "reject"] });
    await settle(setup);
    setup.mockInput.pressKey("n");
    await settle(setup);
    expect(workspace.toolApproval).not.toBeNull();
    expect(setup.captureCharFrame()).toContain("Reject with feedback");
    await setup.mockInput.typeText("try yarn");
    await settle(setup);
    expect(decisions).toEqual([]);
    expect(workspace.toolApproval).not.toBeNull();
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(decisions).toEqual([["t1", "reject"]]);
    expect(workspace.chatDraft).toBe("try yarn");
    setup.renderer.destroy();
  });

  it("y still approves outside the feedback stage", async () => {
    const decisions = fakeApprovalBridge();
    const setup = await mountApp();
    setWorkspace("toolApproval", { id: "t2", tool: "bash", description: "ls", options: ["once", "reject"] });
    await settle(setup);
    setup.mockInput.pressKey("y");
    await settle(setup);
    expect(decisions).toEqual([["t2", "once"]]);
    setup.renderer.destroy();
  });
});

describe("activity tray digits (#4)", () => {
  it("3 switches the tab of the focused /logs pane and types nothing", async () => {
    const setup = await mountApp();
    actions.setActivityTab("agent-logs");
    actions.openPane("logs");
    await settle(setup);
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    setup.mockInput.pressKey("3");
    await settle(setup);
    expect(workspace.activityTab).toBe("events");
    expect(workspace.chatDraft).toBe("");
    setup.renderer.destroy();
  });

  it("the bare activityOpen flag (no tray on screen) leaves digits to the composer", async () => {
    const setup = await mountApp();
    actions.setActivityTab("agent-logs");
    actions.toggleActivityConsole(); // e.g. set by an agent plan / showJobLogs
    await settle(setup);
    await setup.mockInput.typeText("2 more");
    await settle(setup);
    expect(workspace.chatDraft).toBe("2 more");
    expect(workspace.activityTab).toBe("agent-logs");
    setup.renderer.destroy();
  });

  it("a docked but unfocused /logs pane leaves digits to the composer", async () => {
    const setup = await mountApp();
    actions.setActivityTab("agent-logs");
    actions.openPane("logs");
    await settle(setup);
    await setup.mockInput.typeText("3 things");
    await settle(setup);
    expect(workspace.chatDraft).toBe("3 things");
    expect(workspace.activityTab).toBe("agent-logs");
    setup.renderer.destroy();
  });
});

describe("Ctrl+N / Ctrl+P yield to overlay list navigation (#5)", () => {
  it("Ctrl+N with an agent question pending opens no new tab", async () => {
    const setup = await mountApp();
    setWorkspace("askUser", { id: "q1", question: "pick one", options: ["a", "b", "c"] });
    await settle(setup);
    const tabs = workspace.chatTabs.length;
    setup.mockInput.pressKey("n", { ctrl: true });
    await settle(setup);
    expect(workspace.chatTabs.length).toBe(tabs);
    expect(workspace.askUser).not.toBeNull();
    setup.renderer.destroy();
  });

  it("Ctrl+N with the palette open opens no new tab", async () => {
    const setup = await mountApp();
    actions.openCommandPalette();
    await settle(setup);
    const tabs = workspace.chatTabs.length;
    setup.mockInput.pressKey("n", { ctrl: true });
    await settle(setup);
    expect(workspace.chatTabs.length).toBe(tabs);
    expect(workspace.commandPaletteOpen).toBe(true);
    setup.renderer.destroy();
  });
});

describe("research pane Tab (#6)", () => {
  it("Tab on a docked unfocused research pane moves the keys into the pane", async () => {
    const setup = await mountApp();
    actions.openPane("research");
    await settle(setup);
    expect(workspace.paneFocus).toBe(false);
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    expect(workspace.verifyInputFocused).toBe(false);
    setup.renderer.destroy();
  });
});

describe("team overlay Enter (#7)", () => {
  it("a typed draft is sent with Enter while the overlay is open", async () => {
    const setup = await mountApp();
    actions.setTeamDetailId("team-1");
    await settle(setup);
    await setup.mockInput.typeText("hello team");
    await settle(setup);
    expect(workspace.chatDraft).toBe("hello team");
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(workspace.chatDraft).toBe("");
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "hello team")).toBe(true);
    setup.renderer.destroy();
  });
});

describe("palette terminal label (#10/#68)", () => {
  it("ui:terminal advertises the working /term verb, not a dead chord", () => {
    const cmd = getCommands().find((c) => c.id === "ui:terminal");
    expect(cmd?.shortcut).toBe("/term");
    expect(getRegisteredBindings().some((b) => b.key === "ctrl+alt+t")).toBe(false);
  });
});

describe("knowledge proposal keys (#16)", () => {
  it("a approves the first pending proposal in the focused knowledge pane", async () => {
    const decided: unknown[] = [];
    const setup = await mountApp();
    setWorkspace("platform", {
      graphContent: async () => ({ graph_id: "gA", nodes: [{ id: "n1", label: "A node" }], edges: [] }),
      graphProposals: async () => ({ proposals: [{ proposal_id: "p1", graph_id: "gA", summary: "add edge" }] }),
      decideGraphProposal: async (id: string, approved: boolean) => {
        decided.push([id, approved]);
        return {};
      },
    } as never);
    await scienceActions.loadGraphContent("gA");
    await scienceActions.refreshProposals("gA");
    actions.openPane("knowledge");
    setWorkspace("paneFocus", true);
    await settle(setup);
    expect(scienceStore.proposals.length).toBe(1);
    setup.mockInput.pressKey("a");
    await settle(setup);
    expect(decided).toEqual([["p1", true]]);
    setup.renderer.destroy();
  });
});

// ── Plan H-6.3 (2026-10-18): binding lint over the live keymap ─────────────

/** Commands NOT invoked by the boolean lint: in a neutral state they would
 * arm the process exit chord (Ctrl+C / Ctrl+D / Ctrl+Q double press: a
 * second press from any earlier test in the same bun process exits it),
 * start or stop tab bridges, call the bridge, or spawn `bun test` / an
 * editor. Their `KeyHandler` type (() => boolean, checked by tsc over
 * src/ and tests/) is the guarantee for them. */
const NOT_INVOKED = new Set([
  "input.interrupt",
  "input.eof",
  "app.quit",
  "tab.new",
  "tab.close",
  "tab.reopenClosed",
  "tab.next",
  "tab.prev",
  "mode.rotate",
  "codeview.run.tests",
  "codeview.open.editor",
]);

/** Help-legend keys owned by the composer (src/components/chat.tsx), not
 * by the global keymap (docs/r4/TUI_REFERENCE.md "Key ownership"). */
const COMPOSER_LEGEND_KEYS = new Set(["ctrl+r", "ctrl+s", "ctrl+return", "↑", "pgup", "pgdn"]);

/** "Ctrl+Shift+H" → "ctrl+shift+h"; "Esc" → "escape"; "Enter" → "return". */
function normaliseLegendKey(token: string): string {
  const named: Record<string, string> = { esc: "escape", enter: "return" };
  return token
    .toLowerCase()
    .split("+")
    .map((part) => named[part] ?? part)
    .join("+");
}

/** Every key a HELP_CHORDS row names ("a·y·n", "Ctrl+/ Ctrl+.", "Esc Esc"). */
function legendKeys(row: { key: string }): string[] {
  return row.key
    .split(/\s*·\s*|\s+/)
    .filter((token) => token && !token.startsWith("/"))
    .map(normaliseLegendKey);
}

describe("binding lint (H-6.3)", () => {
  it("no default binding is a bare uppercase letter", async () => {
    const { defaultBindings } = await import("../src/keymap");
    expect(defaultBindings().filter((b) => /^[A-Z]$/.test(b.key))).toEqual([]);
  });

  it("every binding names a registered command", async () => {
    const setup = await mountApp();
    const { getRegisteredHandlers } = await import("../src/keymap");
    const commands = new Set(Object.keys(getRegisteredHandlers()));
    expect(getRegisteredBindings().filter((b) => !commands.has(b.cmd))).toEqual([]);
    setup.renderer.destroy();
  });

  it("every handler returns a boolean (true consumes, false yields)", async () => {
    const setup = await mountApp();
    const { getRegisteredHandlers } = await import("../src/keymap");
    const notBoolean = Object.entries(getRegisteredHandlers())
      .filter(([name]) => !NOT_INVOKED.has(name))
      .map(([name, run]) => [name, typeof run()] as const)
      .filter(([, kind]) => kind !== "boolean");
    expect(notBoolean).toEqual([]);
    setup.renderer.destroy();
  });

  it("every help-legend key is a registered binding or a composer key", async () => {
    const setup = await mountApp();
    const { HELP_CHORDS } = await import("../src/lib/help-rows");
    const bound = new Set(getRegisteredBindings().map((b) => b.key));
    const orphans = HELP_CHORDS.flatMap(legendKeys).filter(
      (key) => !bound.has(key) && !COMPOSER_LEGEND_KEYS.has(key),
    );
    expect(orphans).toEqual([]);
    setup.renderer.destroy();
  });

  it("the help overlay's live keymap rows list every registered binding", async () => {
    const setup = await mountApp();
    const { buildHelpRows } = await import("../src/lib/help-rows");
    const { groupBindings, whichKeyRows } = await import("../src/lib/which-key");
    const bindings = getRegisteredBindings();
    const keyRows = whichKeyRows(groupBindings(bindings), Number.MAX_SAFE_INTEGER);
    const text = new Set(buildHelpRows({ entries: [], keyRows, projectSubs: [] }).map((r) => r.text.trim()));
    const missing = bindings.filter((b) => !text.has(`${b.key} → ${b.cmd}`));
    expect(missing).toEqual([]);
    setup.renderer.destroy();
  });
});
