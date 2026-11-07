/**
 * Stabilization lane `focus` (2026-10-21), title screen: it fits short
 * terminals (the overflowing column hid the input and segfaulted OpenTUI's
 * native renderer at ~22 rows), keeps a skipped draft, records its prompt,
 * and yields Esc and the keys to Help drawn over it.
 */
import { describe, it, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, activeTab, workspace } from "../src/stores";
import { SplashOverlay, splashBudget } from "../src/components/splash";
import { splashLinesWithLegacy } from "../src/lib/splash";
import { promptHistory } from "../src/lib/prompt-history";
import { installStabHarness, mountApp, settle } from "./helpers/stab-focus-harness";

installStabHarness();

// ── (c) the title screen ───────────────────────────────────────────────────

describe("title screen (c)", () => {
  const total = splashLinesWithLegacy("0.0.0").length;

  it("the legacy wave shows only when the whole column fits", () => {
    expect(splashBudget(24, total).wave).toBe(false);
    expect(splashBudget(36, total).wave).toBe(false);
    expect(splashBudget(total + 6, total).wave).toBe(true);
    // Every budget leaves room for the 6-row input block.
    for (let h = 12; h <= 60; h++) expect(splashBudget(h, total).rows + 6).toBeLessThanOrEqual(Math.max(h, 6));
  });

  for (const [w, h] of [
    [80, 24],
    [100, 30],
    [64, 22],
    [60, 20],
  ] as const) {
    it(`${w}x${h}: the input, its hint and the logo are on screen`, async () => {
      const setup = await testRender(() => <SplashOverlay />, { width: w, height: h });
      await setup.renderOnce();
      const frame = setup.captureCharFrame();
      expect(frame).toContain("Ask anything");
      expect(frame).toContain("enter to begin");
      expect(frame).toContain("██████╗");
      setup.renderer.destroy();
    });
  }

  it("Esc on the splash carries the typed text into the composer draft", async () => {
    const setup = await mountApp();
    actions.openSplash();
    await settle(setup, 150);
    await setup.mockInput.typeText("a carefully typed question");
    await settle(setup);
    setup.mockInput.pressEscape();
    await settle(setup, 150);
    expect(workspace.splashOpen).toBe(false);
    expect(activeTab().chatDraft).toBe("a carefully typed question");
    setup.renderer.destroy();
  });

  it("a prompt submitted from the splash enters prompt history", async () => {
    const setup = await testRender(() => <SplashOverlay />, { width: 120, height: 40 });
    actions.openSplash();
    await settle(setup);
    await setup.mockInput.typeText("/definitely-not-a-command splash");
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(promptHistory()).toContain("/definitely-not-a-command splash");
    setup.renderer.destroy();
  });

  it("F12 over the splash: Esc closes Help first, then typing reaches the splash input", async () => {
    const setup = await mountApp(120, 45);
    actions.openSplash();
    await settle(setup, 150);
    actions.openHelp();
    await settle(setup);
    setup.mockInput.pressEscape();
    await settle(setup, 150);
    expect([workspace.helpOpen, workspace.splashOpen]).toEqual([false, true]);
    await setup.mockInput.typeText("hello");
    await settle(setup);
    expect(setup.captureCharFrame()).toContain("hello");
    setup.renderer.destroy();
  });
});
