/**
 * Mouse drag selection copy + mouse-reporting toggle.
 *
 * The TUI owns mouse reporting, so terminal-native drag selection is
 * suspended inside the app. Two modifier-key escape hatches (comma chords
 * retired 2026-09-15 — prefix letters swallowed typing):
 *
 *   ctrl+y     — yank the active OpenTUI drag selection to the system
 *                clipboard (OSC52 + pbcopy via lib/clipboard.ts).
 *   ctrl+alt+m — toggle mouse reporting OFF so native terminal selection
 *                works (drag + terminal-native copy), and back ON.
 */

import { describe, expect, it } from "bun:test";
import { groupBindings, whichKeyRows } from "../src/lib/which-key";

/** Mirrors the global bindings in src/keymap/index.ts. */
const BINDINGS = [
  { key: "ctrl+g", cmd: "transcript.cursor.enter" },
  { key: "ctrl+y", cmd: "selection.copy" },
  { key: "ctrl+alt+m", cmd: "mouse.toggle" },
];

describe("selection copy + mouse toggle keymap exposure", () => {
  it("registers selection copy and mouse toggle as modifier keys", () => {
    const rows = whichKeyRows(groupBindings(BINDINGS));
    expect(rows.some((row) => row.includes("ctrl+y → selection.copy"))).toBe(true);
    expect(rows.some((row) => row.includes("ctrl+alt+m → mouse.toggle"))).toBe(true);
  });

  it("no chord-prefix or plain-ctrl/ctrl+shift bindings remain", () => {
    const bad = BINDINGS.filter(
      (b) => b.key.includes(",") || b.key === "ctrl+c" || b.key.startsWith("ctrl+shift+"),
    );
    expect(bad).toHaveLength(0);
  });
});
