/**
 * Palette Enter runs the TYPED command (stabilization 2026-10-21, slash-c#0
 * / chat#5): /undo, /compact, /session, /graph and /workflow have real
 * handlers but no palette row (manifest slashRow false), so Enter used to
 * run the top fuzzy row instead — typed /undo ran /bg, /graph ran /lineage,
 * '/session fork' ran /fork and '/graph zzbogus' was dropped. Also pins the
 * card's height to the terminal (resize-render#5): at 80x24 and 60x20 the
 * fixed 24-row card ran past the bottom and hid its range footer.
 */

import { describe, it, test, expect, beforeEach, afterEach, spyOn } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { paletteEnterAction } from "../src/lib/slash-palette";
import { paletteHeightBudget } from "../src/components/command-palette";
import { dispatchSlashCommand, type SlashCommandEntry } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";

const CATALOG: SlashCommandEntry[] = [
  { name: "bg", help: "Background work", category: "session", takesArgs: false },
  { name: "lineage", help: "Lineage graph", category: "data", takesArgs: true },
  { name: "sessions", help: "Sessions", category: "session", takesArgs: true },
  { name: "fork", help: "Fork the session", category: "session", takesArgs: false },
  { name: "health", help: "Platform health", category: "status", takesArgs: false, aliases: ["hc"] },
];
const ROWS = CATALOG.map((e) => `slash:${e.name}`);

describe("paletteEnterAction", () => {
  test("a row-less manifest command runs as typed, never the fuzzy row", () => {
    expect(paletteEnterAction("undo", CATALOG, ROWS, "slash:bg")).toEqual({ line: "/undo" });
    expect(paletteEnterAction("graph", CATALOG, ROWS, "slash:lineage")).toEqual({ line: "/graph" });
    expect(paletteEnterAction("session", CATALOG, ROWS, "slash:sessions")).toEqual({ line: "/session" });
  });

  test("its args reach it", () => {
    expect(paletteEnterAction("session fork", CATALOG, ROWS, "slash:fork")).toEqual({ line: "/session fork" });
    expect(paletteEnterAction("compact help", CATALOG, ROWS, "slash:bg")).toEqual({ line: "/compact help" });
    expect(paletteEnterAction("graph zzbogus", CATALOG, [], undefined)).toEqual({ line: "/graph zzbogus" });
  });

  test("an exact row name runs its own row, whatever is highlighted", () => {
    expect(paletteEnterAction("health", CATALOG, ROWS, "slash:bg")).toEqual({ id: "slash:health" });
    expect(paletteEnterAction("hc", CATALOG, ROWS, "slash:bg")).toEqual({ id: "slash:health" });
  });

  test("a fragment still runs the highlighted suggestion", () => {
    expect(paletteEnterAction("heal", CATALOG, ROWS, "slash:health")).toEqual({ id: "slash:health" });
  });

  test("zero rows: the line goes to the dispatcher (it names the unknown command)", () => {
    expect(paletteEnterAction("zzbogus", CATALOG, [], undefined)).toEqual({ line: "/zzbogus" });
    expect(paletteEnterAction("   ", CATALOG, [], undefined)).toBeNull();
  });
});

describe("paletteHeightBudget", () => {
  test("the card fits below its 20% top offset with the status bar clear", () => {
    for (const rows of [20, 24, 30, 44, 60]) {
      const { maxHeight, rowCap } = paletteHeightBudget(rows);
      expect(Math.floor(rows * 0.2) + maxHeight).toBeLessThanOrEqual(rows - 2);
      expect(rowCap).toBeGreaterThan(0);
    }
    expect(paletteHeightBudget(60).maxHeight).toBe(24);
  });
});

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
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function renderAt(width: number, height: number) {
  const setup = await testRender(() => <App forceOffline={true} />, { width, height });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();
  return setup;
}

describe("CommandPaletteOverlay", () => {
  it("Enter on a typed '/undo' dispatches /undo, not the fuzzy top row", async () => {
    const setup = await renderAt(160, 60);
    const spy = spyOn(actions, "dispatchSlashFromChat").mockImplementation(async () => ({ kind: "handled" }));
    try {
      actions.openCommandPalette("/undo");
      await setup.renderOnce();
      setup.mockInput.pressEnter();
      await setup.renderOnce();
      expect(spy).toHaveBeenCalledWith("/undo");
    } finally {
      spy.mockRestore();
      setup.renderer.destroy();
    }
  });

  for (const [width, height] of [
    [80, 24],
    [60, 20],
  ] as const) {
    it(`keeps the range footer on screen at ${width}x${height}, even paged to the end`, async () => {
      const setup = await renderAt(width, height);
      actions.openCommandPalette();
      await setup.renderOnce();
      expect(/\d+–\d+ of \d+/.test(setup.captureCharFrame())).toBe(true);
      for (let i = 0; i < 16; i++) {
        setup.mockInput.pressArrow("down");
        await setup.renderOnce();
      }
      const frame = setup.captureCharFrame();
      const footer = frame.split("\n").findIndex((line) => /\d+–\d+ of \d+/.test(line));
      expect(footer).toBeGreaterThan(0);
      // The footer sits above the bottom (status bar) row.
      expect(footer).toBeLessThan(height - 1);
      setup.renderer.destroy();
    });
  }
});

// Typed exactly, /session now reaches its own handler — which answered a
// bare /session or /session help with "Unknown slash command: /session".
describe("/session without a verb", () => {
  it("prints its usage, not 'Unknown slash command'", async () => {
    delete process.env.R4_TUI_BACKEND;
    setCompanionBridgeForTests({ ensureStarted: async () => {}, request: async () => ({}) } as never);
    try {
      for (const line of ["/session", "/session help", "/session zz"]) {
        const result = await dispatchSlashCommand(line);
        expect(result.kind).toBe("error");
        expect(result.kind === "error" && result.message).toContain("usage: /session view | pin | unpin");
      }
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});
