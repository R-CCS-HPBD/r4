/**
 * Terminal pane: key interception that makes the focused terminal behave
 * like a real terminal.
 *
 * Installed as a `keyInput.prependListener` AFTER the R4 keymap was created,
 * so this listener runs FIRST in the global keypress dispatch; handled keys
 * are `stopPropagation()`ed so neither the console keymap nor the chat
 * composer ever sees them (Ctrl+C must reach the shell as SIGINT, Esc must
 * reach vim, …).
 *
 * Focus contract (2026-10-03 revision — click-only focus):
 *   - every key goes to the PTY, including Esc / Tab / Ctrl+C / arrows;
 *     there is NO keyboard way out of the shell — focus moves by mouse
 *     click alone (click the chat column to release; documented in
 *     TUI_REFERENCE §2).
 *   - Ctrl+Q (quit chord), F12 / Ctrl+Shift+H (help) and Ctrl+Alt+M (mouse
 *     reporting toggle — otherwise a mouse-less user could never get one
 *     back) still pass through.
 *   - when an overlay is open the terminal acts unfocused (overlay owns
 *     keys) — one Esc clears the overlay, focus stays on the terminal.
 *   - an exited shell eats keys except `r` (restart) and Esc (close pane).
 *   - Shift+PgUp/PgDn scroll the view through the shell's scrollback
 *     buffer (never sent to the shell); in the alternate screen they are
 *     passed through untouched.
 */

import { workspace, actions, activeTab } from "../stores";
import { keyToBytes } from "./keys";
import { termSession } from "./pty-session";

interface KeyEventLike {
  name: string;
  ctrl: boolean;
  meta: boolean;
  shift: boolean;
  sequence?: string;
  option?: boolean;
  stopPropagation(): void;
}

interface PasteEventLike {
  bytes: Uint8Array;
  stopPropagation(): void;
}

interface KeyInputLike {
  prependListener(event: "keypress" | "paste", listener: (ev: KeyEventLike | PasteEventLike) => void): void;
}

export interface TerminalInterceptRenderer {
  keyInput: KeyInputLike;
}

/** Overlays whose own inputs must keep working while the terminal floats
 *  beneath them. Tracks the Esc-sweep families in keymap/index.ts. */
export function terminalKeysBlocked(): boolean {
  return [
    workspace.splashOpen,
    workspace.commandPaletteOpen,
    workspace.slashBarOpen,
    workspace.slashPickerOpen,
    workspace.helpOpen,
    workspace.quitDialogOpen,
    activeTab().toolApproval != null,
    activeTab().askUser != null,
    workspace.listOverlay != null,
    workspace.tourOpen,
    workspace.startActionsMenuOpen,
    workspace.rewindMenuOpen,
    workspace.startResearch != null,
    workspace.btw.open,
    workspace.citationPreview != null,
    workspace.venuePickerOpen,
    workspace.remoteSourcePickerOpen,
    workspace.webAccessPrompt != null,
    workspace.searchServerOffer,
    workspace.teamDetailId != null,
    workspace.agentDetailId != null,
    workspace.endpointDetailId != null,
    workspace.execDetailId != null,
    workspace.workflowDefId != null,
  ].some(Boolean);
}

export function terminalFocused(): boolean {
  return workspace.paneView === "term" && workspace.paneFocus && !terminalKeysBlocked();
}

/** Keys that belong to the console even while the terminal is focused. */
function isConsoleKey(key: KeyEventLike): boolean {
  if (key.name === "q" && key.ctrl && !key.meta && !key.shift) return true; // quit chord
  if (key.name === "f12" && !key.ctrl && !key.meta) return true; // help
  if (key.name === "h" && key.ctrl && key.shift) return true; // help fallback
  if (key.name === "m" && key.ctrl && key.meta) return true; // mouse-reporting toggle
  return false;
}

export function handleTerminalKey(key: KeyEventLike): void {
  if (isConsoleKey(key)) return; // quit/help/mouse-toggle keep working
  const status = termSession.status();
  if (status.state === "exited") {
    // Dead shell: the pane still eats keys (running apps never hand keys to
    // chat), with one restart verb + Esc CLOSING the pane (the shell is
    // gone; there is nothing to attach to).
    if (key.name === "r" && !key.ctrl && !key.meta) {
      termSession.restart();
    } else if (key.name === "escape") {
      actions.closePane();
    }
    key.stopPropagation();
    return;
  }
  if (status.state !== "running") {
    key.stopPropagation();
    return;
  }
  // View scrollback (view-only; the shell is never told). Shift+PgUp/PgDn
  // mirrors the outer terminal's own scroll keys. Not in the alternate
  // screen — fullscreen apps get the raw keys instead.
  if ((key.name === "pageup" || key.name === "pagedown") && key.shift && !termSession.alternateActive) {
    // scrollBy: positive = further back into scrollback.
    termSession.scrollBy(key.name === "pageup" ? termSession.rows - 1 : -(termSession.rows - 1));
    key.stopPropagation();
    return;
  }
  const bytes = keyToBytes(key, { applicationCursorKeys: termSession.applicationCursorKeys });
  if (bytes === null) return; // unmapped combos fall through to the console
  if (bytes.length > 0) termSession.write(bytes);
  key.stopPropagation();
}

export function handleTerminalPaste(event: PasteEventLike): void {
  const text = new TextDecoder().decode(event.bytes);
  termSession.paste(text);
  event.stopPropagation();
}

let installed = false;

/** Install the interceptor once. Registered AFTER the keymap so prepend
 *  ordering makes this the first global keypress listener. */
export function installTerminalKeyInterceptor(renderer: TerminalInterceptRenderer): void {
  if (installed) return;
  installed = true;
  renderer.keyInput.prependListener("keypress", (ev) => {
    if (!terminalFocused()) return;
    handleTerminalKey(ev as KeyEventLike);
  });
  renderer.keyInput.prependListener("paste", (ev) => {
    if (!terminalFocused()) return;
    handleTerminalPaste(ev as PasteEventLike);
  });
}

/** Test hook: re-arm the installer after a suite wiped module state. */
export function resetTerminalInterceptorForTest(): void {
  installed = false;
}
