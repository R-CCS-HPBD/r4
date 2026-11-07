/**
 * w3-tui item 9 (2026-10-21): "漢kZabc" on screen while "漢Zabc" was sent.
 * Not a TUI repaint bug: tests/pty-live/screen.ts (the PTY emulator) has no
 * wide-character support — it advanced one column for a CJK glyph, so the
 * cell OpenTUI leaves to the terminal (the glyph's second column) kept a
 * stale character from the placeholder. A wide-aware xterm fed the same
 * bytes shows "漢Zabc" (screen.ts now models wide cells; see its tests).
 * This pin holds the renderer's own frame and the draft to the same text.
 */
import { describe, expect, it } from "bun:test";
import { activeTab } from "../src/stores";
import { installStabHarness, mountApp, settle } from "./helpers/stab-focus-harness";

installStabHarness();

describe("CJK edit in the composer", () => {
  it("left ×3, Backspace, Z after 漢字abc: draft and frame both read 漢Zabc", async () => {
    const setup = await mountApp(80, 24);
    await setup.mockInput.typeText("漢字abc");
    await settle(setup);
    for (let i = 0; i < 3; i++) setup.mockInput.pressArrow("left");
    await settle(setup);
    setup.mockInput.pressBackspace();
    await settle(setup);
    await setup.mockInput.typeText("Z");
    await settle(setup);
    const row = setup.captureCharFrame().split("\n").find((l) => l.includes("›")) ?? "";
    setup.renderer.destroy();
    expect(activeTab().chatDraft).toBe("漢Zabc");
    expect(row).toContain("›漢Zabc");
  });
});
