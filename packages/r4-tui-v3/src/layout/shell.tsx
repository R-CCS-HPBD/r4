/**
 * Root console shell — V4 chat-centric frame.
 *
 * Chat is primal and always mounted. The detail pane is summoned:
 * docked right at 45% (≥140 cols) / 38% (100–139) by default, DRAG-RESIZABLE
 * by grabbing the pane's border bar (workspace.paneWidthOverride), or shown
 * as a full-width sheet below 100 cols so narrow terminals degrade honestly
 * instead of crushing the chat. Chat stays mounted under the sheet to keep
 * streaming + scroll.
 *
 * Pointer rules (2026-10-03 revision):
 *   - clicking the TERMINAL pane focuses it (its box onMouseDown);
 *   - clicking anywhere in the chat column releases terminal focus —
 *     the terminal is the only view whose focus moves by click alone.
 */

import { StatusBar } from "./status-bar";
import { DetailPane } from "./detail-pane";
import { workspace, actions, setWorkspace } from "../stores";
import { ChatPanel } from "../components/chat";
import { ChatTabStrip } from "../components/chat-tab-strip";
import { SplashOverlay } from "../components/splash";
import { useTerminalDims } from "../lib/terminal-dims";
import { createEffect, createSignal } from "solid-js";
import {
  paneLayout,
  paneFraction,
  resolvePaneCols,
} from "../lib/pane-layout";
import { paneBounds } from "../lib/pane-measure";
import {
  chatColumnWidth,
  paneColumnWidth,
  syncChatWidth,
  syncPaneWidth,
  syncTermWidth,
} from "../lib/term-width";
import { palette } from "../themes";

export function Shell() {
  const dims = useTerminalDims();
  const layout = () => paneLayout(dims().width);
  const paneOpen = () => workspace.paneView !== null;
  /** Effective pane width: drag override → clamped cols; else layout fraction. */
  const paneWidth = (): number | `${number}%` => {
    if (workspace.paneWidthOverride !== null && layout() !== "sheet") {
      return resolvePaneCols(dims().width, workspace.paneWidthOverride);
    }
    return paneFraction(layout());
  };
  // Grab-zone column for the border drag (0-based): the pane's MEASURED
  // left bar (publishPaneBounds — never the width estimate; the flexbox
  // engine's resolved width differs from percentage math). Accept one
  // column to the left too — the bar is one column wide.
  const paneLeft = () => {
    const measured = paneBounds();
    if (measured.x > 0) return measured.x;
    if (typeof paneWidth() === "number") return dims().width - (paneWidth() as number);
    const frac = layout() === "dock45" ? 0.45 : 0.38;
    return dims().width - Math.floor(dims().width * frac);
  };
  const [dragging, setDragging] = createSignal(false);
  // Single source of truth for surface widths — pure helpers (tool-activity
  // wrap, approval previews, panel caps) read these synchronously instead of
  // hardcoding column counts. Re-runs on resize AND pane open/close.
  createEffect(() => {
    const w = dims().width;
    const open = paneOpen();
    syncTermWidth(w);
    syncChatWidth(chatColumnWidth(w, open, workspace.paneWidthOverride, paneBounds()));
    syncPaneWidth(
      open && workspace.paneWidthOverride !== null && layout() !== "sheet"
        ? resolvePaneCols(w, workspace.paneWidthOverride)
        : paneColumnWidth(w, open),
    );
  });
  const sheet = () => paneOpen() && layout() === "sheet";
  // Sheet layout (narrow terminals): the pane is FULL-SCREEN, so chat is
  // invisible — entering sheet hands the keys to the pane once (the honest
  // full-screen semantic the pre-2026-10-06 focus model had everywhere).
  // Only fires on the docked→sheet TRANSITION: a Tab out inside sheet mode
  // is already refused by the keymap toggle guard.
  let wasSheet = false;
  createEffect(() => {
    const now = sheet();
    if (now && !wasSheet && !workspace.paneFocus) setWorkspace("paneFocus", true);
    wasSheet = now;
  });

  /** Mouse plumbing: border drags resize the pane; a click in the chat
   *  column releases terminal focus. Nothing else reacts to plain clicks. */
  const onShellMouseDown = (event: { x: number; preventDefault(): void; stopPropagation(): void }) => {
    if (paneOpen() && layout() !== "sheet") {
      const left = paneLeft();
      if (event.x >= left - 1 && event.x <= left) {
        setDragging(true);
        event.preventDefault(); // never start a text selection on the bar
        event.stopPropagation();
        return;
      }
    }
    // Release pane focus only for clicks LEFT of the pane — pane clicks
    // themselves set focus (DetailPane/TerminalPane onMouseDown) and may
    // bubble up here after: x-zone, not "who fired first", decides.
    // (2026-10-06: generalized from the terminal-only release — clicking
    // the chat column now returns the keys to the composer for ANY view.)
    if (
      workspace.paneView !== null &&
      workspace.paneFocus &&
      !(paneOpen() && layout() !== "sheet" && event.x >= paneLeft() - 1)
    ) {
      setWorkspace("paneFocus", false);
    }
  };
  const onShellMouseMove = (event: { x: number }) => {
    if (!dragging()) return;
    actions.setPaneWidthOverride(dims().width - event.x);
  };
  // The drop position counts too — the last move is rarely where the button
  // actually came up.
  const onShellMouseUp = (event: { x: number }) => {
    if (!dragging()) return;
    actions.setPaneWidthOverride(dims().width - event.x);
    setDragging(false);
  };

  return (
    <box
      width="100%"
      height="100%"
      flexDirection="column"
      onMouseDown={onShellMouseDown}
      onMouseMove={onShellMouseMove}
      onMouseDrag={onShellMouseMove}
      onMouseUp={onShellMouseUp}
      onMouseDragEnd={onShellMouseUp}
    >
      <box width="100%" flexGrow={1} flexDirection="row" overflow="hidden">
        <box
          flexGrow={1}
          flexDirection="column"
          overflow="hidden"
          onMouseDown={() => {
            // Clicking anywhere in the chat column releases pane focus
            // (closer to the pointer than the shell-level handler, so rows
            // that stopPropagation on their own verb still release).
            if (workspace.paneView !== null && workspace.paneFocus) {
              setWorkspace("paneFocus", false);
            }
          }}
        >
          <ChatTabStrip />
          <ChatPanel />
        </box>
        {paneOpen() && layout() !== "sheet" && (
          <DetailPane width={paneWidth()} />
        )}
      </box>
      <StatusBar />
      {sheet() && (
        <box
          position="absolute"
          top={0}
          left={0}
          width="100%"
          height="100%"
          backgroundColor={palette.bg}
          flexDirection="column"
        >
          <box flexGrow={1} flexDirection="column" overflow="hidden">
            <DetailPane width="100%" />
          </box>
          <StatusBar />
        </box>
      )}
      {workspace.splashOpen && <SplashOverlay />}
    </box>
  );
}
