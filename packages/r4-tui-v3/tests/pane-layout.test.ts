/**
 * A3 — Responsive pane breakpoints.
 * ≥140 → 45% dock · 100–139 → 38% dock · <100 → sheet over chat.
 */

import { describe, expect, test } from "bun:test";
import { paneLayout, paneFraction } from "../src/lib/pane-layout";

describe("paneLayout breakpoints", () => {
  test("docked 45% at and above 140 cols", () => {
    expect(paneLayout(160)).toBe("dock45");
    expect(paneLayout(140)).toBe("dock45");
    expect(paneFraction("dock45")).toBe("45%");
  });

  test("docked 38% between 100 and 139 cols", () => {
    expect(paneLayout(139)).toBe("dock38");
    expect(paneLayout(100)).toBe("dock38");
    expect(paneFraction("dock38")).toBe("38%");
  });

  test("sheet below 100 cols", () => {
    expect(paneLayout(99)).toBe("sheet");
    expect(paneLayout(80)).toBe("sheet");
    expect(paneLayout(60)).toBe("sheet");
    expect(paneFraction("sheet")).toBe("100%");
  });
});
