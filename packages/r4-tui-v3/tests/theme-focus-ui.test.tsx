/**
 * Theme picker live-preview + focus integration (headless testRender):
 *
 *  1. Bare /theme → picker opens; arrow keys preview themes LIVE.
 *  2. After every preview step the theme list must still be painted (regression
 *     for "list overwritten by background paint during preview").
 *  3. Esc restores the opener theme; Enter keeps.
 *  4. After the picker closes, keyboard focus returns to the chat input
 *     (regression for "/models leaves dead keys until click").
 *
 * Runs the real App tree through @opentui/solid's testRender, so these are
 * behavior pins, not store-unit assertions.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { applyTheme, currentThemeName, listThemes } from "../src/themes";
import { themePicker } from "../src/commands/slash-pickers";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  process.env.R4_CONFIG_DIR = process.env.R4_CONFIG_DIR || "/tmp/r4-tui-focus-test";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
  applyTheme("aurora", { persist: false });
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 150));
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
  applyTheme("aurora", { persist: false });
});

async function boot() {
  const setup = await testRender(() => <App />, { width: 140, height: 40 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();
  return setup;
}

describe("theme picker live preview", () => {
  it("arrow keys preview themes and the list stays painted", async () => {
    const setup = await boot();
    actions.openSlashPicker(themePicker());
    await setup.renderOnce();

    const opened = setup.captureCharFrame();
    expect(opened).toContain("Choose a color theme");
    expect(opened).toContain("dracula");

    // Two arrow-downs: aurora → tokyo-night → nord (THEMES order).
    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    expect(currentThemeName()).toBe(listThemes()[1]!.name);

    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    expect(currentThemeName()).toBe(listThemes()[2]!.name);

    // THE PAINT REGRESSION: after preview-driven remounts the theme rows must
    // still be visible (was: background paint overwrote the whole list).
    const frameAfterPreview = setup.captureCharFrame();
    expect(frameAfterPreview).toContain("Choose a color theme");
    expect(frameAfterPreview).toContain("dracula");
    expect(frameAfterPreview).toContain("rose-pine");

    // Esc cancels → opener theme restored. (Headless parser holds a lone ESC
    // byte as a sequence start — flush with a delay before asserting.)
    setup.mockInput.pressEscape();
    await new Promise((r) => setTimeout(r, 150));
    await setup.renderOnce();
    expect(currentThemeName()).toBe("aurora");
    expect(workspace.slashPickerOpen).toBe(false);

    setup.renderer.destroy();
  });

  it("Enter keeps the previewed theme", async () => {
    const setup = await boot();
    actions.openSlashPicker(themePicker());
    await setup.renderOnce();

    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    const previewed = listThemes()[1]!.name;
    expect(currentThemeName()).toBe(previewed);

    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await new Promise((r) => setTimeout(r, 50));
    await setup.renderOnce();
    expect(workspace.slashPickerOpen).toBe(false);
    expect(currentThemeName()).toBe(previewed);

    setup.renderer.destroy();
  });
});

describe("chat focus return after pickers", () => {
  it("typing lands in the chat input after Enter closes the picker", async () => {
    const setup = await boot();
    actions.openSlashPicker(themePicker());
    await setup.renderOnce();

    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await new Promise((r) => setTimeout(r, 50));
    await setup.renderOnce();
    expect(workspace.slashPickerOpen).toBe(false);

    // THE FOCUS REGRESSION: without the input-remount these keystrokes die on
    // the destroyed KeyTrap instead of reaching the chat input.
    await setup.mockInput.typeText("focus-check");
    await setup.renderOnce();
    expect(workspace.chatDraft).toContain("focus-check");

    setup.renderer.destroy();
  });

  it("typing lands in the chat input after Esc closes the picker", async () => {
    const setup = await boot();
    actions.openSlashPicker(themePicker());
    await setup.renderOnce();

    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    setup.mockInput.pressEscape();
    await new Promise((r) => setTimeout(r, 150));
    await setup.renderOnce();
    expect(workspace.slashPickerOpen).toBe(false);

    await setup.mockInput.typeText("esc-focus");
    await setup.renderOnce();
    expect(workspace.chatDraft).toContain("esc-focus");

    setup.renderer.destroy();
  });
});
