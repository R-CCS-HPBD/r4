import { describe, it, expect, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { workspace, actions } from "../src/stores";
import { HelpOverlay } from "../src/components/help-overlay";
import { getRegisteredBindings } from "../src/keymap";
import { buildHelpRows } from "../src/lib/help-rows";
import {
  localSlashCommands,
  PROJECT_SUBCOMMANDS,
  ENDPOINT_SUBCOMMANDS,
  TEAMUP_SUBCOMMANDS,
} from "../src/commands/slash";

describe("F12 help overlay", () => {
  beforeEach(() => {
    actions.resetWorkspace();
  });

  it("toggle flips the flag and the overlay mounts content when open", async () => {
    const setup = await testRender(() => <HelpOverlay />, { width: 120, height: 40 });
    // Reset between tests: window pane/title changes are what we assert —
    // the frame shows content only once the flag flips.
    actions.toggleHelp();
    expect(workspace.helpOpen).toBe(true);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("help — F12 opens it");
    expect(frame).toContain("Slash catalog");
    actions.toggleHelp();
    expect(workspace.helpOpen).toBe(false);
  });

  it("/help command routes to the same overlay", async () => {
    expect(workspace.helpOpen).toBe(false);
    await actions.dispatchSlashFromChat("/help");
    expect(workspace.helpOpen).toBe(true);
    actions.closeAllOverlays();
    expect(workspace.helpOpen).toBe(false);
  });
});


describe("help rows — FULL catalog, nothing truncated", () => {
  const entries = localSlashCommands();
  const keyRows = ["◇ misc", "  f12 → help.toggle", "  ctrl+p → palette.open"];
  const rows = buildHelpRows({ entries, keyRows, projectSubs: PROJECT_SUBCOMMANDS });
  const text = rows.map((r) => r.text).join("\n");

  it("every catalog command appears with its help text (no 86-char slice)", () => {
    for (const entry of entries) {
      const row = rows.find(
        (r) => r.text.includes(`/${entry.name}`) && r.text.includes(`— ${entry.help}`),
      );
      expect(row, `missing help row for /${entry.name}`).toBeDefined();
    }
    // The verbs the old slice(0, 86) hid — regression pins.
    for (const name of ["usage", "workflows", "loops", "rag", "source", "traces", "models", "mcp"]) {
      expect(text).toContain(`/${name}`);
    }
  });

  it("subcommand rows render for project / endpoint / teamup", () => {
    for (const sub of PROJECT_SUBCOMMANDS) {
      expect(text).toContain(`· ${sub.name} — ${sub.help}`);
    }
    for (const sub of ENDPOINT_SUBCOMMANDS) {
      expect(text).toContain(`· ${sub.name} — ${sub.help}`);
    }
    for (const sub of TEAMUP_SUBCOMMANDS) {
      expect(text).toContain(`· ${sub.name} — ${sub.help}`);
    }
  });

  it("every keymap row passes through (no 14-row cap)", () => {
    for (const row of keyRows) {
      expect(text).toContain(row);
    }
  });

  it("category headers carry live counts", () => {
    expect(text).toContain(`◇ endpoint (${entries.filter((e) => e.category === "endpoint").length})`);
    expect(text).toContain(`Slash catalog (${entries.length} verbs`);
  });
});

describe("help overlay scrolling", () => {
  beforeEach(() => {
    actions.resetWorkspace();
  });

  it("End key reaches the rows the old fixed box clipped off-screen", async () => {
    const setup = await testRender(() => <HelpOverlay />, { width: 120, height: 40 });
    actions.toggleHelp();
    await setup.renderOnce();
    const top = setup.captureCharFrame();
    expect(top).toContain("help — F12 opens it");
    // The footer row sits far below the 40-row viewport — invisible at top…
    expect(top).not.toContain("errors here NEVER stand in for input");
    setup.mockInput.pressKey("END");
    await setup.renderOnce();
    const bottom = setup.captureCharFrame();
    // …and reachable via End (scrollbox), along with trailing catalog verbs.
    expect(bottom).toContain("errors here NEVER stand in for input");
    setup.mockInput.pressKey("HOME");
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("help — F12 opens it");
    setup.renderer.destroy();
  });

  it("Esc closes the overlay from the scroll KeyTrap", async () => {
    const setup = await testRender(() => <HelpOverlay />, { width: 120, height: 40 });
    actions.toggleHelp();
    await setup.renderOnce();
    expect(workspace.helpOpen).toBe(true);
    setup.mockInput.pressEscape();
    await setup.renderOnce();
    await new Promise((r) => setTimeout(r, 200));
    await setup.renderOnce();
    expect(workspace.helpOpen).toBe(false);
    setup.renderer.destroy();
  });
});

describe("F12 binding registration", () => {
  it("f12 + ctrl+shift+h are both registered against help.toggle", async () => {
    // The binding table populates at keymap build; render App once to drive it
    // (same factory the live frame uses — no hand-rolled mock table here).
    const { App } = await import("../src/app");
    await testRender(() => <App />, { width: 120, height: 40 });
    const keymapBindings = getRegisteredBindings();
    const helpBindings = keymapBindings.filter(
      (b) => b.cmd === "help.toggle",
    );
    const keys = helpBindings.map((b) => b.key).sort();
    expect(keys).toEqual(["ctrl+shift+h", "f12"]);
  });
});
