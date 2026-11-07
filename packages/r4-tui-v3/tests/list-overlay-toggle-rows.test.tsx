/**
 * 2026-10-01 pins for the toggleable rows in the floating list overlay:
 *
 * 1. Arrow-key re-renders must NOT duplicate row text. A JSX-CHILD StyledText
 *    (t`…`) appends fresh chunks on every re-render — after N cursor moves
 *    each row's label appeared N times. Rows now pass StyledText through the
 *    children PROP (replace semantics).
 * 2. On/off state renders as ● (active) / ○ (inactive) glyphs, never the
 *    [on]/[off] text chips.
 * 3. Same-name registry + user-config MCP rows stay distinct rows; the
 *    registry row carries no chip, the local row keeps its ●/○.
 */

import { describe, it, expect, beforeEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions } from "../src/stores";
import { ListOverlay } from "../src/components/list-overlay";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  actions.resetWorkspace();
});

function fakeBridge() {
  return {
    ensureStarted: async () => {},
    request: async () => ({}),
  };
}

function occurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

describe("list overlay toggle rows", () => {
  it("skills rows render ●/○ once, and stay single after arrow keys", async () => {
    setCompanionBridgeForTests(fakeBridge() as never);
    actions.setSkillInventory([
      { name: "analysis", source: "builtin", hasMcp: false, description: "", active: false, tools: ["a", "b", "c"] },
      { name: "ara-compiler", source: "builtin", hasMcp: false, description: "", active: true },
    ]);
    try {
      const setup = await testRender(() => <ListOverlay />, { width: 120, height: 40 });
      actions.openListOverlay("skills");
      await setup.renderOnce();

      const initial = setup.captureCharFrame();
      expect(occurrences(initial, "analysis · builtin")).toBe(1);
      expect(occurrences(initial, "ara-compiler · builtin")).toBe(1);
      expect(initial).toContain("○ analysis");
      expect(initial).toContain("● ara-compiler");
      expect(initial).not.toContain("[on]");
      expect(initial).not.toContain("[off]");

      // The bug: every arrow key appended the row text again.
      for (let i = 0; i < 4; i++) {
        setup.mockInput.pressKey("down" as never);
        await setup.renderOnce();
      }
      const moved = setup.captureCharFrame();
      expect(occurrences(moved, "analysis · builtin")).toBe(1);
      expect(occurrences(moved, "ara-compiler · builtin")).toBe(1);
      setup.renderer.destroy();
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  it("mcp rows: same-name registry row does not shadow the local ●/○ row", async () => {
    setCompanionBridgeForTests(fakeBridge() as never);
    actions.setMcpServers([
      { name: "fetch", server_id: "srv-1", status: "registered ✓ (agent-visible as registry entry)", source: "registry" },
      { name: "fetch", status: "disabled (off)", source: "user-config:user", enabled: false },
    ]);
    try {
      const setup = await testRender(() => <ListOverlay />, { width: 120, height: 40 });
      actions.openListOverlay("mcp");
      await setup.renderOnce();

      const frame = setup.captureCharFrame();
      // Registry row: no chip. Local row: ○ chip (enabled: false).
      expect(occurrences(frame, "fetch ·")).toBe(2);
      expect(frame).toContain("○ fetch");
      for (let i = 0; i < 3; i++) {
        setup.mockInput.pressKey("down" as never);
        await setup.renderOnce();
      }
      const moved = setup.captureCharFrame();
      expect(occurrences(moved, "fetch ·")).toBe(2);
      expect(moved).toContain("○ fetch");
      setup.renderer.destroy();
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});
