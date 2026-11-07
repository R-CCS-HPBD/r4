/**
 * D-2 (c3-tui 2026-10-21): the docked pane lays out at its designed width
 * (45% at ≥140 columns, 38% at 100–139 — TUI_REFERENCE §2), and /sessions
 * rows are fitted to the pane's MEASURED width.
 *
 * On the base the pane's percentage width kept OpenTUI's default
 * flexShrink=1, so the chat column's wide auto basis squeezed it to ~30%
 * (49 of 160 columns), and the rows, sized from the 45% estimate, were
 * squeezed cell by cell until the chips painted over each other
 * ("Fcompletmirror10-21").
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { Shell } from "../src/layout/shell";
import { paneBounds } from "../src/lib/pane-measure";
import { displayWidth } from "../src/lib/markdown-wrap";
import { NAME_MIN_COLS, sessionRowCells } from "../src/lib/session-row";
import { resetWorkspace, actions, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

const RECORD = {
  id: "fxsess01-0000-0000-0000-000000000000",
  name: "fixture session 1 with a deliberately long descriptive name for the pane",
  model: "fixture-model",
  created_at: "2026-10-21T10:00:00",
  updated_at: "2026-10-21T11:01:00",
  venue: "frontend",
  state: "completed",
  sync: "mirrored" as const,
};

function rowWidth(cells: ReturnType<typeof sessionRowCells>): number {
  const parts = [cells.head, "F", cells.state, cells.sync, cells.date].filter(Boolean);
  return parts.reduce((sum, p) => sum + displayWidth(p), 0) + parts.length - 1;
}

describe("sessionRowCells (pure)", () => {
  it("never exceeds the inner width, from roomy to tiny panes", () => {
    for (let inner = 20; inner <= 120; inner++) {
      expect(rowWidth(sessionRowCells(RECORD, inner))).toBeLessThanOrEqual(inner);
    }
  });

  it("keeps every chip when the pane is roomy", () => {
    const cells = sessionRowCells(RECORD, 69);
    expect(cells.state).toBe("completed");
    expect(cells.sync).toBe("mirrored");
    expect(cells.date).toBe("10-21 11:01");
    expect(cells.head.startsWith("fxsess01  fixture session 1")).toBe(true);
  });

  it("drops the sync badge first, then the state chip, before the name goes below its minimum", () => {
    const mid = sessionRowCells(RECORD, 50);
    expect(mid.sync).toBe("");
    expect(mid.state).toBe("completed");
    const narrow = sessionRowCells(RECORD, 40);
    expect(narrow.state).toBe("");
    expect(displayWidth(narrow.head) - "fxsess01  ".length).toBeGreaterThanOrEqual(NAME_MIN_COLS);
  });

  it("cuts a CJK name by display columns", () => {
    const cells = sessionRowCells({ ...RECORD, name: "計算結果の可視化と解析のセッション名がとても長い" }, 40);
    expect(rowWidth(cells)).toBeLessThanOrEqual(40);
    expect(cells.head).toContain("…");
  });
});

describe("docked /sessions pane", () => {
  for (const [cols, frac] of [
    [160, 0.45],
    [120, 0.38],
  ] as const) {
    it(`lays out at its designed ${frac * 100}% at ${cols} columns, rows intact`, async () => {
      setWorkspace("sessionsList", [RECORD] as never);
      setWorkspace("paneView", "sessions" as never);
      const setup = await testRender(() => <Shell />, { width: cols, height: 30 });
      for (let i = 0; i < 4; i++) await setup.renderOnce();
      expect(Math.abs(paneBounds().width - Math.floor(cols * frac))).toBeLessThanOrEqual(1);
      const row = setup
        .captureCharFrame()
        .split("\n")
        .find((l) => l.includes("fxsess01  ") && l.includes("10-21 11:01"));
      expect(row).toBeDefined();
      // Cells separated by one space each — never painted over each other.
      expect(row!).toMatch(/fxsess01 {2}fixture session 1.*… F (completed )?(mirrored )?10-21 11:01/);
      setup.renderer.destroy();
    });
  }
});
