/**
 * term-width — the single source of truth for surface widths.
 *
 * Pins the fallback chain (synced → stdout.columns → 120), the chat/pane
 * column math per layout mode, and the panel-cap derivation that replaced
 * the console's hardcoded column counts.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
  chatColumnWidth,
  chatContentWidth,
  chatWidth,
  contentWidth,
  paneColumnWidth,
  paneContentWidth,
  panePanelWidth,
  paneWidth,
  resetTermWidthForTest,
  syncChatWidth,
  syncPaneWidth,
  syncTermWidth,
  termWidth,
} from "../src/lib/term-width";

beforeEach(() => {
  resetTermWidthForTest();
});

describe("termWidth fallback chain", () => {
  it("falls back to stdout.columns or 120 when nothing synced", () => {
    const cols = process.stdout.columns;
    expect(termWidth()).toBe(cols && cols > 0 ? cols : 120);
  });

  it("prefers the synced width over stdout.columns", () => {
    syncTermWidth(177);
    expect(termWidth()).toBe(177);
    expect(contentWidth(4)).toBe(173);
  });

  it("ignores non-positive sync values", () => {
    syncTermWidth(0);
    syncTermWidth(-5);
    expect(termWidth()).toBe(process.stdout.columns || 120);
  });
});

describe("chat/pane width sync", () => {
  it("chatWidth and paneWidth fall back to the terminal width", () => {
    syncTermWidth(160);
    expect(chatWidth()).toBe(160);
    expect(paneWidth()).toBe(160);
  });

  it("uses synced chat/pane widths when present", () => {
    syncTermWidth(200);
    syncChatWidth(110);
    syncPaneWidth(90);
    expect(chatWidth()).toBe(110);
    expect(paneWidth()).toBe(90);
    expect(chatContentWidth(4)).toBe(106);
    expect(paneContentWidth(4)).toBe(86);
  });
});

describe("column math per pane layout", () => {
  it("gives chat the full width when the pane is closed", () => {
    expect(chatColumnWidth(200, false)).toBe(200);
    expect(paneColumnWidth(200, false)).toBe(0);
  });

  it("splits 55/45 on wide terminals (dock45)", () => {
    expect(chatColumnWidth(200, true)).toBe(110);
    expect(paneColumnWidth(200, true)).toBe(90);
  });

  it("splits 62/38 on mid terminals (dock38)", () => {
    expect(chatColumnWidth(120, true)).toBe(74);
    expect(paneColumnWidth(120, true)).toBe(45);
  });

  it("sheet mode gives chat the full width below 100 cols", () => {
    expect(chatColumnWidth(90, true)).toBe(90);
    expect(paneColumnWidth(90, true)).toBe(90);
  });
});

describe("panel caps", () => {
  it("scales fractional panels off the synced pane width", () => {
    syncTermWidth(200);
    syncPaneWidth(90);
    // ⅓ bottom-panel column: (90-4)*0.33 − panel chrome ≈ 22
    expect(panePanelWidth(0.33)).toBe(Math.floor(86 * 0.33) - 6);
    // full-width explainability tab
    expect(panePanelWidth(1, 8)).toBe(86 - 4);
  });

  it("never drops below the floor on tiny panes", () => {
    syncTermWidth(100);
    syncPaneWidth(10);
    expect(panePanelWidth(0.33)).toBe(20);
    expect(paneContentWidth(4)).toBe(24);
  });

  it("at 120 cols, caps land near the legacy hardcoded values", () => {
    // Continuity pin: the old fixed caps (36 / ~110) were tuned around
    // 120-col terminals — the derived caps must stay in that neighborhood.
    syncTermWidth(120);
    syncPaneWidth(120);
    const third = panePanelWidth(0.33);
    const full = panePanelWidth(1, 8);
    expect(third).toBeGreaterThanOrEqual(28);
    expect(third).toBeLessThanOrEqual(40);
    expect(full).toBeGreaterThanOrEqual(100);
    expect(full).toBeLessThanOrEqual(120);
  });
});
