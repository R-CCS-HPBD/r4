/**
 * Stabilization 2026-10-21 (w3-tui, item 1) — a multi-line paste on the
 * title screen keeps its line breaks. The splash input is single-line and
 * dropped every newline, so a pasted script arrived as one run-on line; the
 * paste now moves to the composer (draft + paste) with its breaks.
 */
import { describe, it, expect } from "bun:test";
import { actions, activeTab, workspace } from "../src/stores";
import { installStabHarness, mountApp, settle } from "./helpers/stab-focus-harness";

installStabHarness();

describe("splash multi-line paste", () => {
  it("a CR/LF paste arrives in the composer with its breaks (typed prefix kept)", async () => {
    const setup = await mountApp();
    actions.openSplash();
    await settle(setup, 150);
    await setup.mockInput.typeText("run this: ");
    await settle(setup);
    await setup.mockInput.pasteBracketedText("line one\r\nline two\rline three");
    await settle(setup, 150);
    expect(workspace.splashOpen).toBe(false);
    expect(activeTab().chatDraft).toBe("run this: line one\nline two\nline three");
    setup.renderer.destroy();
  });

  it("a single-line paste stays on the splash input", async () => {
    const setup = await mountApp();
    actions.openSplash();
    await settle(setup, 150);
    await setup.mockInput.pasteBracketedText("just one line");
    await settle(setup, 150);
    expect(workspace.splashOpen).toBe(true);
    expect(setup.captureCharFrame()).toContain("just one line");
    setup.renderer.destroy();
  });
});
