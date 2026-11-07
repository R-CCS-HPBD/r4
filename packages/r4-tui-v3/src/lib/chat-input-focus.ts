/**
 * The composer-focus predicate, pure on the workspace store (extracted from
 * chat.tsx so pins can assert it without mounting the Panel; 2026-10-06).
 */

import { workspace } from "../stores";
import { keyboardLayerOpen } from "../keymap/store-claims";

export function chatInputFocused(): boolean {
  const overlaysOpen = [
    // Every store layer that owns typed input or list navigation — palette,
    // slash bar/picker, help, quit dialog, approval, ask-user, list overlay,
    // start-research, /connect wizard, start-actions and rewind menus, tour,
    // venue and remote-source pickers, and the title screen (it owns the
    // input until the first message or Esc). Derived from the store-claims
    // table (stab-focus, 2026-10-21): the hand-kept list here missed the
    // /connect wizard and both venue pickers, so closing them left the
    // composer dead (no false→true edge, no remount).
    keyboardLayerOpen(),
    workspace.btw.open,
    workspace.citationPreview != null,
    // Pane focus (2026-10-06 revision): the composer yields ONLY while the
    // pane actually OWNS the keys (paneFocus). A docked-but-unfocused pane
    // leaves chat fully usable — the old blanket "pane open = pane owns
    // keys" disabled the composer whenever a side panel was up (user
    // report). paneFocus falling back to false trips the epoch-edge
    // remount in chat.tsx so focus returns (the dead-focus class that
    // surfaced in the L4 PTY wave). A CLICK in the chat column also
    // releases pane focus (shell-level handler); clicking the pane moves
    // it in.
    workspace.paneView != null && workspace.paneFocus,
  ].some(Boolean);
  return !overlaysOpen;
}
