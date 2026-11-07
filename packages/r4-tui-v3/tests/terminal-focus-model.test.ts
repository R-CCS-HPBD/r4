/**
 * Terminal pane: click-only focus model + key interception semantics
 * (no live PTY — session methods are spied).
 */

import { describe, expect, test, beforeEach, spyOn } from "bun:test";
import { workspace, setWorkspace, resetWorkspace, actions } from "../src/stores";
import {
  handleTerminalKey,
  handleTerminalPaste,
  terminalFocused,
  terminalKeysBlocked,
} from "../src/terminal/interceptor";
import { termSession } from "../src/terminal/pty-session";
import { resolvePaneCols, PANE_MIN_COLS } from "../src/lib/pane-layout";

interface FakeKey {
  name: string;
  ctrl: boolean;
  meta: boolean;
  shift: boolean;
  stopped: boolean;
  stopPropagation(): void;
}

function fakeKey(partial: Partial<FakeKey> & { name: string }): FakeKey {
  const k: FakeKey = {
    ctrl: false, meta: false, shift: false, stopped: false,
    ...partial,
    stopPropagation() { k.stopped = true; },
  };
  return k;
}

function focusTerminal(): void {
  setWorkspace("paneView", "term");
  setWorkspace("paneFocus", true);
}

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  termSession.state = "idle";
  termSession.exitCode = undefined;
  termSession.scrollOffset = 0;
});

describe("terminal open/focus store semantics", () => {
  test("every view opens UNFOCUSED — chat keeps the keys (2026-10-06 model)", () => {
    actions.openPane("term");
    expect(workspace.paneView).toBe("term");
    expect(workspace.paneFocus).toBe(false);
    actions.closePane();
    // Non-term views opened focused-on-open until 2026-10-06 — that made a
    // docked pane disable the composer (user report).
    actions.openPane("tasks");
    expect(workspace.paneFocus).toBe(false);
    actions.closePane();
  });

  test("togglePaneFocus never touches the terminal view", () => {
    actions.openPane("term");
    actions.togglePaneFocus(); // would focus — refused
    expect(workspace.paneFocus).toBe(false);
  });

  test("drag-resize helper clamps to min and chat reserve", () => {
    expect(resolvePaneCols(140, null)).toBe(63); // 45% default
    expect(resolvePaneCols(113, null)).toBe(42); // 38% default
    expect(resolvePaneCols(140, 10)).toBe(PANE_MIN_COLS);
    expect(resolvePaneCols(140, 900)).toBe(100); // chat keeps ≥40
    expect(resolvePaneCols(140, 77)).toBe(77);
    expect(resolvePaneCols(80, null)).toBe(80); // sheet spans
    expect(resolvePaneCols(80, 50)).toBe(80); // sheet ignores override
  });
});

describe("terminal key interception", () => {
  test("unfocused / overlay states report correctly", () => {
    expect(terminalFocused()).toBe(false);
    focusTerminal();
    expect(terminalFocused()).toBe(true);
    setWorkspace("commandPaletteOpen", true);
    expect(terminalKeysBlocked()).toBe(true);
    expect(terminalFocused()).toBe(false);
    setWorkspace("commandPaletteOpen", false);
    setWorkspace("paneFocus", false);
    expect(terminalFocused()).toBe(false);
  });

  test("running shell: letters, ctrl and esc go to the PTY, never to chat", () => {
    focusTerminal();
    termSession.state = "running";
    const write = spyOn(termSession, "write").mockImplementation(() => {});
    const a = fakeKey({ name: "a" });
    handleTerminalKey(a);
    expect(write).toHaveBeenCalledWith("a");
    expect(a.stopped).toBe(true);
    const ctrlC = fakeKey({ name: "c", ctrl: true });
    handleTerminalKey(ctrlC);
    expect(write).toHaveBeenCalledWith("\x03");
    expect(ctrlC.stopped).toBe(true);
    // Esc reaches the shell (vim) and must NOT close the pane.
    const esc = fakeKey({ name: "escape" });
    handleTerminalKey(esc);
    expect(write).toHaveBeenCalledWith("\x1b");
    expect(esc.stopped).toBe(true);
    expect(workspace.paneView).toBe("term");
    expect(workspace.paneFocus).toBe(true);
    write.mockRestore();
  });

  test("no key releases terminal focus (click-only contract)", () => {
    focusTerminal();
    termSession.state = "running";
    const write = spyOn(termSession, "write").mockImplementation(() => {});
    // The old Ctrl+Alt+T hatch is gone: ctrl+alt+letter combos are ordinary
    // bytes to the shell now.
    const hatch = fakeKey({ name: "t", ctrl: true, meta: true });
    handleTerminalKey(hatch);
    expect(hatch.stopped).toBe(true);
    expect(write).toHaveBeenCalledWith("\x1b\x14"); // ESC + control-T
    expect(workspace.paneFocus).toBe(true);
    write.mockRestore();
  });

  test("quit/help/mouse chords pass through untouched", () => {
    focusTerminal();
    termSession.state = "running";
    const write = spyOn(termSession, "write").mockImplementation(() => {});
    for (const key of [
      fakeKey({ name: "q", ctrl: true }),
      fakeKey({ name: "f12" }),
      fakeKey({ name: "h", ctrl: true, shift: true }),
      // Mouse-reporting toggle must stay reachable: without it a mouse-less
      // user could never leave terminal focus under the click-only model.
      fakeKey({ name: "m", ctrl: true, meta: true }),
    ]) {
      handleTerminalKey(key);
      expect(key.stopped).toBe(false);
    }
    expect(write).not.toHaveBeenCalled();
    write.mockRestore();
  });

  test("dead shell: r restarts, Esc closes the pane, everything else swallowed", () => {
    focusTerminal();
    termSession.state = "exited";
    termSession.exitCode = 0;
    const restart = spyOn(termSession, "restart").mockImplementation(() => {});
    const write = spyOn(termSession, "write").mockImplementation(() => {});
    const a = fakeKey({ name: "a" });
    handleTerminalKey(a);
    expect(a.stopped).toBe(true);
    expect(write).not.toHaveBeenCalled();
    const r = fakeKey({ name: "r" });
    handleTerminalKey(r);
    expect(restart).toHaveBeenCalled();
    const esc = fakeKey({ name: "escape" });
    handleTerminalKey(esc);
    expect(workspace.paneView).toBe(null);
    restart.mockRestore();
    write.mockRestore();
  });

  test("Shift+PgUp scrolls the view, never the shell", () => {
    focusTerminal();
    termSession.state = "running";
    const scroll = spyOn(termSession, "scrollBy").mockImplementation(() => {});
    const pgup = fakeKey({ name: "pageup", shift: true });
    handleTerminalKey(pgup);
    expect(pgup.stopped).toBe(true);
    expect(scroll).toHaveBeenCalledWith(termSession.rows - 1);
    scroll.mockRestore();
  });

  test("paste payloads honour bracketed-paste and stop propagation", () => {
    focusTerminal();
    termSession.state = "running";
    const paste = spyOn(termSession, "paste").mockImplementation(() => {});
    let stopped = false;
    handleTerminalPaste({
      bytes: new TextEncoder().encode("echo hi\n"),
      stopPropagation() { stopped = true; },
    });
    expect(paste).toHaveBeenCalledWith("echo hi\n");
    expect(stopped).toBe(true);
    paste.mockRestore();
  });
});
