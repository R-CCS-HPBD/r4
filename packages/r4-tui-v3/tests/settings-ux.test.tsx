import { describe, expect, it, beforeEach } from "bun:test";
import { actions, workspace } from "../src/stores";
import { keyboardRows, backendLabelFor } from "../src/routes/settings";

describe("Settings space context", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    actions.closeAllOverlays();
  });

  it("does not clear object selection when entering Settings", () => {
    actions.selectObject("obj-test");
    actions.setSpace("settings");
    expect(workspace.paneView).toBe("settings");
    expect(workspace.selectedObjectId).toBe("obj-test");
  });

  it("keeps selection after leaving Settings for Research", () => {
    actions.selectObject("obj-test");
    actions.setSpace("settings");
    actions.setSpace("research");
    expect(workspace.selectedObjectId).toBe("obj-test");
  });
});

describe("Settings keyboard rows (L4-post — live keymap registry)", () => {
  async function liveApp() {
    // Registered-bindings populate at keymap build — mount App once to drive
    // the same factory the live frame runs.
    const { testRender } = await import("@opentui/solid");
    const { App } = await import("../src/app");
    await testRender(() => <App />, { width: 120, height: 40 });
    return;
  }

  it("serves every registered row (live registry, never a drift)", async () => {
    await liveApp();
    const rows = keyboardRows(100_000);
    expect(rows.length).toBeGreaterThan(10);
    const blob = rows.join("\n");
    for (const cmd of ["help.toggle", "app.quit", "overlay.close", "palette.open", "slash.open"]) {
      expect(blob).toContain(cmd);
    }
    expect(blob).toContain("f12");
    expect(blob).not.toContain("r4_tui_demo");
  });

  it("truncates long registries with an explicit overflow line (budgeted)", () => {
    // Budget pass: a forced tiny cap must overflow with the explicit line —
    // no keymap build needed (the truncation acts on whatever rows exist).

    const all = keyboardRows(100_000);
    const tiny = all.length > 3 ? keyboardRows(3) : [];
    if (all.length > 3) {
      expect(tiny.some((r) => r.startsWith("… +"))).toBe(true);
    } else {
      expect(tiny.length).toBeLessThanOrEqual(3);
    }
  });

  it("labels backend from demoMode flag", () => {
    expect(backendLabelFor(true)).toMatch(/offline/i);
    expect(backendLabelFor(false)).toMatch(/live/i);
  });
});

describe("Settings rows in the narrow side pane (stabilization 2026-10-21, panes-overlays#11)", () => {
  it("keeps every label: value pair whole at ~46 cols", async () => {
    const { testRender } = await import("@opentui/solid");
    const { SettingsRoute } = await import("../src/routes/settings");
    const { setWorkspace } = await import("../src/stores");
    actions.resetWorkspace();
    actions.setMcpServers([]);
    const skill = (name: string) => ({ name, source: "builtin", hasMcp: false, description: "" });
    setWorkspace("skills", Array.from({ length: 29 }, (_, i) => skill(`s${i}`)));
    const setup = await testRender(() => <SettingsRoute />, { width: 46, height: 60 });
    try {
      await setup.renderOnce();
      const frame = setup.captureCharFrame();
      for (const pair of ["Endpoints: 0", "Models: 0", "Jobs: 0", "Skills: 29", "Model: auto"]) {
        expect(frame).toContain(pair);
      }
      expect(frame).toContain("Skills (29)");
      expect(frame).toContain("MCP servers (0)");
      expect(frame).toMatch(/Gateway: \S+/);
    } finally {
      setup.renderer.destroy();
      actions.resetWorkspace();
    }
  });
});

describe("Settings Preferences (issue #217 — a real preference surface)", () => {
  type Setup = Awaited<ReturnType<typeof import("@opentui/solid").testRender>>;
  const savedBackend = process.env.R4_TUI_BACKEND;
  const realDispatch = actions.dispatchSlashFromChat;

  async function settle(setup: Setup, ms = 60): Promise<void> {
    await new Promise((r) => setTimeout(r, ms));
    await setup.renderOnce();
  }

  async function settingsPane(focused: boolean): Promise<Setup> {
    process.env.R4_TUI_BACKEND = "offline";
    const { testRender } = await import("@opentui/solid");
    const { App } = await import("../src/app");
    const { setWorkspace } = await import("../src/stores");
    actions.resetWorkspace();
    const setup = await testRender(() => <App />, { width: 160, height: 50 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    actions.openPane("settings");
    setWorkspace("paneFocus", focused);
    await settle(setup, 120);
    return setup;
  }

  function teardown(setup: Setup): void {
    setup.renderer.destroy();
    (actions as Record<string, unknown>).dispatchSlashFromChat = realDispatch;
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
    actions.resetWorkspace();
  }

  it("shows each preference with its value and scope; the subtitle is no longer status-only", async () => {
    const setup = await settingsPane(false);
    try {
      const frame = setup.captureCharFrame();
      expect(frame).toContain("Preferences");
      expect(frame).toContain("Completion bell: on (this session)");
      expect(frame).toContain("Ambient event cards: on (this session)");
      expect(frame).toMatch(/Theme: \S+ \(saved\)/);
      expect(frame).not.toContain("Status and shortcuts");
    } finally {
      teardown(setup);
    }
  });

  it("re-reads file-backed values (status line) when the transcript grows", async () => {
    const { writeStatuslineCommand } = await import("../src/lib/statusline");
    const setup = await settingsPane(false);
    try {
      expect(setup.captureCharFrame()).toContain("Status line: off (saved)");
      writeStatuslineCommand("date +%H:%M");
      actions.sendSystemNotice("status line set"); // what /statusline <cmd> posts
      await settle(setup);
      expect(setup.captureCharFrame()).toContain("Status line: date +%H:%M (saved)");
    } finally {
      writeStatuslineCommand("off");
      teardown(setup);
    }
  });

  it("Enter on the focused pane toggles the completion bell in place", async () => {
    const setup = await settingsPane(true);
    try {
      expect(workspace.completionBellEnabled).toBe(true);
      setup.mockInput.pressEnter();
      await settle(setup);
      expect(workspace.completionBellEnabled).toBe(false);
      expect(setup.captureCharFrame()).toContain("Completion bell: off (this session)");
    } finally {
      teardown(setup);
    }
  });

  it("↓↓ Enter on Theme opens the /theme picker through the slash dispatcher", async () => {
    const setup = await settingsPane(true);
    const lines: string[] = [];
    (actions as Record<string, unknown>).dispatchSlashFromChat = async (line: string) => {
      lines.push(line);
      return { kind: "handled" as const };
    };
    try {
      setup.mockInput.pressArrow("down");
      setup.mockInput.pressArrow("down");
      await settle(setup);
      setup.mockInput.pressEnter();
      await settle(setup);
      expect(lines).toEqual(["/theme"]);
    } finally {
      teardown(setup);
    }
  });

  it("a click on a row acts once (ambient cards toggles off, not twice)", async () => {
    const setup = await settingsPane(false);
    try {
      const lines = setup.captureCharFrame().split("\n");
      const row = lines.findIndex((l) => l.includes("Ambient event cards:"));
      expect(row).toBeGreaterThan(-1);
      await setup.mockMouse.click(lines[row]!.indexOf("Ambient event cards:") + 2, row);
      await settle(setup);
      expect(workspace.ambientCardsEnabled).toBe(false);
    } finally {
      teardown(setup);
    }
  });

  it("Tab moves through the panels and keeps the keys in the pane", async () => {
    const setup = await settingsPane(true);
    try {
      setup.mockInput.pressTab();
      await settle(setup);
      expect(workspace.paneFocus).toBe(true);
      // Skills has the keys now: Enter no longer reaches the bell row.
      setup.mockInput.pressEnter();
      await settle(setup);
      expect(workspace.completionBellEnabled).toBe(true);
      // Skills → MCP → Preferences: the bell row has the keys again.
      setup.mockInput.pressTab();
      await settle(setup);
      setup.mockInput.pressTab();
      await settle(setup);
      expect(workspace.paneFocus).toBe(true);
      setup.mockInput.pressEnter();
      await settle(setup);
      expect(workspace.completionBellEnabled).toBe(false);
    } finally {
      teardown(setup);
    }
  });
});
