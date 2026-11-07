/**
 * Plan H-6.4 (2026-10-18): the key-ownership matrix through the FULL
 * <App/> (global keymap + focused renderables). For each layer, Esc / Tab /
 * Enter / letters behave per the precedence table in docs/r4/TUI_REFERENCE.md
 * "Key ownership":
 *
 *   modal overlay trap > claims (Esc / Tab / search) > pane-focused
 *   bindings > global bindings > composer
 *
 * and the Esc ladder: transient → splash → self-contained overlays →
 * detail overlays → modal layer (closes the modals only; the turn and the
 * pane stay, stab-focus 2026-10-21) → local owners
 * (yield) → pane. One fresh App per cell, so cells never share state.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";

type Setup = Awaited<ReturnType<typeof testRender>>;

interface Ctx {
  setup: Setup;
  decisions: unknown[][];
  tabs: number;
}

type Key = "escape" | "tab" | "enter" | "ctrl+n" | "ctrl+o" | "ctrl+r" | "j" | "n" | "r" | "y";

interface Cell {
  key: Key;
  /** What the precedence table says happens. */
  then: string;
  check: (ctx: Ctx) => void;
}

interface Layer {
  name: string;
  arrange: (ctx: Ctx) => Promise<void> | void;
  cells: Cell[];
}

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
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 80): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

const PRESS: Record<Key, (setup: Setup) => void> = {
  escape: (s) => s.mockInput.pressEscape(),
  tab: (s) => s.mockInput.pressTab(),
  enter: (s) => s.mockInput.pressEnter(),
  "ctrl+n": (s) => s.mockInput.pressKey("n", { ctrl: true }),
  "ctrl+o": (s) => s.mockInput.pressKey("o", { ctrl: true }),
  "ctrl+r": (s) => s.mockInput.pressKey("r", { ctrl: true }),
  j: (s) => s.mockInput.pressKey("j"),
  n: (s) => s.mockInput.pressKey("n"),
  r: (s) => s.mockInput.pressKey("r"),
  y: (s) => s.mockInput.pressKey("y"),
};

async function press(ctx: Ctx, key: Key): Promise<void> {
  PRESS[key](ctx.setup);
  await settle(ctx.setup, 120);
}

/** A bridge that records approval decisions and answers every request. */
function fakeBridge(decisions: unknown[][]): void {
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    shutdown: async () => {},
    request: async () => ({}),
    respondApproval: (id: unknown, decision: unknown) => decisions.push([id, decision]),
  } as never);
}

function seedUserMessage(): void {
  setWorkspace("chatMessages", [
    ...workspace.chatMessages,
    { id: "u-h6", role: "user" as const, content: "earlier prompt", timestamp: new Date().toISOString() },
  ]);
}

function pane(view: "sessions" | "work" | "codeview" | "research", focused: boolean): void {
  actions.openPane(view);
  setWorkspace("paneFocus", focused);
}

function approval(): void {
  setWorkspace("toolApproval", { id: "t1", tool: "bash", description: "ls", options: ["once", "always", "reject"] });
}

const frame = (ctx: Ctx): string => ctx.setup.captureCharFrame();

// ── the table ───────────────────────────────────────────────────────────────

const COMPOSER: Layer = {
  name: "composer (no pane, no overlay)",
  arrange: () => {},
  cells: [
    { key: "r", then: "typed into the draft", check: () => expect(workspace.chatDraft).toBe("r") },
    { key: "tab", then: "no pane: nothing moves", check: () => expect(workspace.paneFocus).toBe(false) },
    {
      key: "escape",
      then: "no claimant: the composer arms Esc Esc, nothing closes",
      check: () => expect([workspace.paneView, workspace.rewindMenuOpen]).toEqual([null, false]),
    },
    {
      key: "ctrl+o",
      then: "a global binding beats the composer",
      check: () => expect(workspace.verboseTranscript).toBe(true),
    },
  ],
};

const PANE_UNFOCUSED: Layer = {
  name: "docked pane, unfocused (sessions)",
  arrange: () => pane("sessions", false),
  cells: [
    {
      key: "n",
      then: "pane letters stay off: typed into the draft",
      check: () => expect([workspace.chatDraft, workspace.venuePickerOpen]).toEqual(["n", false]),
    },
    { key: "tab", then: "the keys move into the pane", check: () => expect(workspace.paneFocus).toBe(true) },
    { key: "escape", then: "the pane closes", check: () => expect(workspace.paneView).toBeNull() },
  ],
};

const PANE_FOCUSED: Layer = {
  name: "docked pane, focused (sessions)",
  arrange: () => pane("sessions", true),
  cells: [
    {
      key: "n",
      then: "a pane-focused binding fires (sessions.new), nothing typed",
      check: () => expect([workspace.venuePickerOpen, workspace.chatDraft]).toEqual([true, ""]),
    },
    { key: "tab", then: "the keys return to the composer", check: () => expect(workspace.paneFocus).toBe(false) },
    { key: "escape", then: "the pane closes", check: () => expect(workspace.paneView).toBeNull() },
  ],
};

const PALETTE: Layer = {
  name: "modal overlay trap (command palette over a focused pane)",
  arrange: () => {
    pane("sessions", true);
    actions.openCommandPalette();
  },
  cells: [
    {
      key: "n",
      then: "the trap takes the letter: no pane verb, no draft",
      check: () => expect([workspace.venuePickerOpen, workspace.chatDraft]).toEqual([false, ""]),
    },
    {
      key: "tab",
      then: "Tab stays in the trap",
      check: () => expect([workspace.commandPaletteOpen, workspace.paneFocus]).toEqual([true, true]),
    },
    {
      key: "ctrl+n",
      then: "list navigation, not a new chat tab",
      check: (ctx) => expect([workspace.chatTabs.length, workspace.commandPaletteOpen]).toEqual([ctx.tabs, true]),
    },
    {
      key: "escape",
      then: "one layer: the palette closes, the pane stays (stab-focus)",
      check: () => expect([workspace.commandPaletteOpen, workspace.paneView]).toEqual([false, "sessions"]),
    },
  ],
};

const HELP: Layer = {
  name: "modal overlay trap (help over a focused pane)",
  arrange: () => {
    pane("sessions", true);
    actions.toggleHelp();
  },
  cells: [
    {
      key: "n",
      then: "no pane verb, no draft",
      check: () => expect([workspace.venuePickerOpen, workspace.chatDraft]).toEqual([false, ""]),
    },
    { key: "tab", then: "Tab stays in the overlay", check: () => expect(workspace.paneFocus).toBe(true) },
    { key: "escape", then: "help closes (modal sweep)", check: () => expect(workspace.helpOpen).toBe(false) },
  ],
};

const VENUE_PICKER: Layer = {
  name: "self-contained overlay (venue picker over a focused pane)",
  arrange: () => {
    pane("sessions", true);
    actions.openVenuePicker();
  },
  cells: [
    {
      key: "escape",
      then: "one layer: the picker closes, the pane stays",
      check: () => expect([workspace.venuePickerOpen, workspace.paneView]).toEqual([false, "sessions"]),
    },
    { key: "tab", then: "Tab stays in the picker", check: () => expect(workspace.paneFocus).toBe(true) },
    { key: "r", then: "no pane verb, no draft", check: () => expect(workspace.chatDraft).toBe("") },
  ],
};

const TOUR: Layer = {
  name: "self-contained overlay (tour over a pane)",
  arrange: () => {
    pane("sessions", false);
    setWorkspace("tourOpen", true);
  },
  cells: [
    {
      key: "escape",
      then: "the tour closes, the pane stays",
      check: () => expect([workspace.tourOpen, workspace.paneView]).toEqual([false, "sessions"]),
    },
  ],
};

const TEAM_DETAIL: Layer = {
  name: "detail overlay (team over the work pane)",
  arrange: () => {
    pane("work", false);
    actions.setTeamDetailId("team-1");
  },
  cells: [
    {
      key: "escape",
      then: "the overlay closes, the pane stays",
      check: () => expect([workspace.teamDetailId, workspace.paneView]).toEqual([null, "work"]),
    },
    {
      key: "j",
      then: "the overlay's claimed verb moves its cursor, nothing typed",
      check: () => expect([workspace.teamDetailCursor, workspace.chatDraft]).toEqual([1, ""]),
    },
    {
      key: "tab",
      then: "a detail overlay claims no Tab: the keys move into the pane",
      check: () => expect([workspace.paneFocus, workspace.teamDetailId]).toEqual([true, "team-1"]),
    },
  ],
};

const APPROVAL: Layer = {
  name: "approval card (claims y / a / n / Enter) over a pane",
  arrange: (ctx) => {
    fakeBridge(ctx.decisions);
    pane("sessions", false);
    approval();
  },
  cells: [
    { key: "y", then: "allow once", check: (ctx) => expect(ctx.decisions).toEqual([["t1", "once"]]) },
    { key: "enter", then: "allow once", check: (ctx) => expect(ctx.decisions).toEqual([["t1", "once"]]) },
    {
      key: "n",
      then: "the feedback stage opens, nothing rejected yet",
      check: (ctx) => {
        expect(ctx.decisions).toEqual([]);
        expect(frame(ctx)).toContain("Reject with feedback");
      },
    },
    {
      key: "tab",
      then: "the card claims Tab: the pane keeps no focus",
      check: () => expect([workspace.paneFocus, workspace.toolApproval?.id]).toEqual([false, "t1"]),
    },
    {
      key: "escape",
      then: "one layer: the card is rejected and closes, the pane stays (stab-focus)",
      check: (ctx) => {
        expect([workspace.toolApproval, workspace.paneView]).toEqual([null, "sessions"]);
        expect(ctx.decisions).toEqual([["t1", "reject"]]);
      },
    },
  ],
};

const APPROVAL_FEEDBACK: Layer = {
  name: "approval card, feedback stage (claims Esc and typed text)",
  arrange: async (ctx) => {
    fakeBridge(ctx.decisions);
    pane("sessions", false);
    approval();
    await settle(ctx.setup);
    await press(ctx, "n");
  },
  cells: [
    {
      key: "escape",
      then: "one layer: back to the card, the approval and the pane stay",
      check: (ctx) => {
        expect([workspace.toolApproval?.id, workspace.paneView]).toEqual(["t1", "sessions"]);
        expect(frame(ctx)).toContain("Tool approval required");
      },
    },
    { key: "y", then: "typed feedback, never an approval", check: (ctx) => expect(ctx.decisions).toEqual([]) },
    { key: "enter", then: "rejects with the feedback", check: (ctx) => expect(ctx.decisions).toEqual([["t1", "reject"]]) },
  ],
};

const COMPOSER_SEARCH: Layer = {
  name: "composer claim: Ctrl+R search with a docked pane",
  arrange: async (ctx) => {
    seedUserMessage();
    pane("sessions", false);
    await settle(ctx.setup);
    await press(ctx, "ctrl+r");
  },
  cells: [
    {
      key: "tab",
      then: "the search claims Tab: the keys stay in the composer",
      check: (ctx) => {
        expect(workspace.paneFocus).toBe(false);
        expect(frame(ctx)).toContain("reverse-i-search");
      },
    },
    {
      key: "escape",
      then: "a local claim yields Esc: the search ends, the pane stays",
      check: (ctx) => {
        expect(frame(ctx)).not.toContain("reverse-i-search");
        expect(workspace.paneView).toBe("sessions");
      },
    },
  ],
};

const COMPOSER_DRAFT: Layer = {
  name: "composer claim: a typed draft with a docked unfocused pane",
  arrange: async (ctx) => {
    pane("sessions", false);
    await settle(ctx.setup);
    await ctx.setup.mockInput.typeText("hello");
  },
  cells: [
    {
      key: "escape",
      then: "the draft clears first, the pane stays",
      check: () => expect([workspace.chatDraft, workspace.paneView]).toEqual(["", "sessions"]),
    },
    {
      key: "tab",
      then: "a draft does not trap the keys (r2 #67)",
      check: () => expect([workspace.paneFocus, workspace.chatDraft]).toEqual([true, "hello"]),
    },
  ],
};

const TRANSIENT: Layer = {
  name: "transient layers (armed revert in the code view)",
  arrange: () => {
    pane("codeview", true);
    setWorkspace("revertArmPath", "a.py");
  },
  cells: [
    {
      key: "escape",
      then: "the armed revert cancels before the pane closes",
      check: () => expect([workspace.revertArmPath, workspace.paneView]).toEqual([null, "codeview"]),
    },
  ],
};

const BTW: Layer = {
  name: "transient layers (/btw over a pane)",
  arrange: () => {
    pane("sessions", false);
    setWorkspace("btw", { open: true, index: 0, exchanges: [] });
  },
  cells: [
    {
      key: "escape",
      then: "/btw closes, the pane stays",
      check: () => expect([workspace.btw.open, workspace.paneView]).toEqual([false, "sessions"]),
    },
  ],
};

const SPLASH: Layer = {
  name: "splash",
  arrange: () => actions.openSplash(),
  cells: [{ key: "escape", then: "skips to chat", check: () => expect(workspace.splashOpen).toBe(false) }],
};

const VERIFY: Layer = {
  name: "local claim: Live verify input in the focused research pane",
  arrange: () => {
    pane("research", true);
    actions.setResearchFocus("verify");
    actions.setVerifyInputFocused(true);
  },
  cells: [
    {
      key: "escape",
      then: "Esc leaves verify, the pane stays",
      check: () => expect([workspace.verifyInputFocused, workspace.paneView]).toEqual([false, "research"]),
    },
  ],
};

const MATRIX: Layer[] = [
  COMPOSER,
  PANE_UNFOCUSED,
  PANE_FOCUSED,
  PALETTE,
  HELP,
  VENUE_PICKER,
  TOUR,
  TEAM_DETAIL,
  APPROVAL,
  APPROVAL_FEEDBACK,
  COMPOSER_SEARCH,
  COMPOSER_DRAFT,
  TRANSIENT,
  BTW,
  SPLASH,
  VERIFY,
];

async function runCell(layer: Layer, cell: Cell): Promise<void> {
  const setup = await testRender(() => <App />, { width: 160, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await settle(setup);
  const ctx: Ctx = { setup, decisions: [], tabs: workspace.chatTabs.length };
  try {
    await layer.arrange(ctx);
    await settle(setup);
    await press(ctx, cell.key);
    cell.check(ctx);
  } finally {
    setup.renderer.destroy();
  }
}

for (const layer of MATRIX) {
  describe(`key matrix: ${layer.name}`, () => {
    for (const cell of layer.cells) {
      it(`${cell.key} → ${cell.then}`, () => runCell(layer, cell));
    }
  });
}
