/**
 * Interactive picker for slash commands that need a selection (e.g. /endpoint up, /models).
 * List rows may carry a `group` key; consecutive same-group rows render under
 * one section header. Bounds-safe for long lists (scroll window) and narrow
 * terminals (width clamp) — the /models per-source picker relies on this.
 */

import { For, Show, createMemo, createSignal } from "solid-js";
import { workspace, actions, setWorkspace } from "../stores";
import { palette } from "../themes";
import type { KeyEvent } from "@opentui/core";
import { useTerminalDims } from "../lib/terminal-dims";
import { applyBridgeStatePatch, getCompanionBridge, mapBridgeStateToWorkspace } from "../bridge";
import { KeyTrap } from "./key-trap";
import { applyOnTab } from "../tabs/registry";
import type { SlashPickerItem } from "../commands/slash-pickers";
import { Floating } from "../ui/Floating";

type Row =
  | { kind: "header"; text: string }
  | { kind: "item"; item: SlashPickerItem; globalIndex: number };

export function SlashPickerOverlay() {
  const picker = () => workspace.slashPicker;
  // Store-held selection: /theme live preview remounts this overlay every
  // highlight (it sits inside the epoch-keyed tree); a createSignal would
  // reset to 0 on every arrow key.
  const selected = () => workspace.slashPickerSelection;
  const setSelected = (i: number) => actions.setSlashPickerSelection(i);

  const items = createMemo<SlashPickerItem[]>(() => picker()?.items ?? []);
  const dims = useTerminalDims();
  const safeSelected = createMemo(() => Math.min(selected(), Math.max(0, items().length - 1)));

  // Rows: inject a header whenever the group key changes.
  const rows = createMemo<Row[]>(() => {
    const out: Row[] = [];
    let lastGroup: string | undefined;
    items().forEach((item, idx) => {
      const g = item.group;
      if (g !== lastGroup) {
        if (g) out.push({ kind: "header", text: g });
        lastGroup = g;
      }
      out.push({ kind: "item", item, globalIndex: idx });
    });
    return out;
  });

  // Bounded scrolling window (height clamps to the terminal).
  const termRows = () => dims().height || 40;
  const termCols = () => dims().width || 120;
  const visibleRows = createMemo(() => Math.min(16, Math.max(6, Math.floor(termRows() * 0.5))));
  const maxOffset = () => Math.max(0, rows().length - visibleRows());
  const [offset, setOffset] = createSignal(0);
  const effectiveOffset = createMemo(() => Math.min(Math.max(offset(), 0), maxOffset()));
  const shownRows = createMemo(() =>
    rows().slice(effectiveOffset(), effectiveOffset() + visibleRows()),
  );
  const pickerWidth = createMemo(() => Math.max(48, Math.floor(termCols() * 0.64)));

  // Keep selection within the visible window.
  const ensureSelectedVisible = (idx: number) => {
    const rowIndex = rows().findIndex((row) => row.kind === "item" && row.globalIndex === idx);
    if (rowIndex < 0) return idx;
    if (rowIndex < effectiveOffset()) setOffset(rowIndex);
    else if (rowIndex >= effectiveOffset() + visibleRows()) setOffset(rowIndex - visibleRows() + 1);
    return idx;
  };

  const close = () => {
    // onCancel (when set) fires inside closeSlashPicker so EVERY close route —
    // global keymap Esc, mouse, programmatic — gets cancel semantics.
    actions.closeSlashPicker();
    setOffset(0);
  };

  /** Enter "keeps" the previewed state: drop the cancel hook before closing. */
  const clearCancelHook = () => {
    const state = picker();
    if (state?.onCancel) setWorkspace("slashPicker", { ...state, onCancel: undefined });
  };

  /** Move the highlight and fire the picker's live-preview hook (if any). */
  const moveHighlight = (idx: number) => {
    const next = ensureSelectedVisible(idx);
    setSelected(next);
    const item = items()[next];
    if (item) picker()?.onHighlight?.(item);
  };

  const runSelection = (item: SlashPickerItem) => {
    const state = picker();
    if (!state) return;
    clearCancelHook();
    close();
    if (state.mode === "set_model") {
      const model = item.modelName ?? item.key;
      // The snapshot belongs to the tab whose bridge switched models: applied
      // to whichever tab is active at answer time, it rebound that tab's
      // session and model badge (stabilization 2026-10-21).
      const tabId = workspace.activeChatTabId;
      void (async () => {
        try {
          const bridge = getCompanionBridge();
          const snapshot = await bridge.request("set_model" as never, { model }, 30_000);
          applyOnTab(tabId, () => {
            applyBridgeStatePatch(mapBridgeStateToWorkspace(snapshot, undefined));
            actions.sendSystemNotice(`model → ${model}`);
          });
        } catch (err) {
          const text = err instanceof Error ? err.message : `Failed to set model: ${String(err)}`;
          applyOnTab(tabId, () => actions.sendSystemNotice(text));
        }
      })();
      return;
    }
    void actions.dispatchSlashFromChat(`${state.commandPrefix}${item.key}`);
  };

  const handleKey = (event: KeyEvent) => {
    if (event.name === "down" || (event.name === "n" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(Math.min(safeSelected() + 1, items().length - 1));
    } else if (event.name === "up" || (event.name === "p" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(Math.max(safeSelected() - 1, 0));
    } else if (event.name === "pageup" || (event.name === "b" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(Math.max(safeSelected() - visibleRows(), 0));
    } else if (event.name === "pagedown" || (event.name === "f" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(Math.min(safeSelected() + visibleRows(), items().length - 1));
    } else if (event.name === "home" || (event.name === "a" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(0);
    } else if (event.name === "end" || (event.name === "e" && event.ctrl)) {
      event.preventDefault();
      moveHighlight(Math.max(0, items().length - 1));
    } else if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      const entry = items()[safeSelected()];
      if (entry) runSelection(entry);
    } else if (event.name === "escape") {
      event.preventDefault();
      close();
    }
  };

  if (!workspace.slashPickerOpen || !picker()) return null;

  return (
    <Floating
      position="absolute"
      width={pickerWidth()}
      height={visibleRows() + 6}
      left={Math.max(2, Math.floor((termCols() - pickerWidth()) / 2))}
      top={Math.max(1, Math.floor(termRows() * 0.18))}
      flexDirection="column"
      padding={1}
      gap={0}
      overflow="hidden"
    >
      <text fg={palette.accent} height={1}>
        {`${picker()!.title} (${items().length})`}
      </text>
      <box
        flexDirection="column"
        width="100%"
        height={visibleRows()}
        overflow="hidden"
        marginTop={1}
        marginBottom={1}
      >
        <For each={shownRows()}>
          {(row) => {
            if (row.kind === "header") {
              return (
                <box flexDirection="row" width="100%" height={1} backgroundColor={palette.bgPanel}>
                  <text fg={palette.fgDim}>{`▾ ${row.text}`}</text>
                </box>
              );
            }
            const isSel = () => row.globalIndex === safeSelected();
            return (
              <box
                flexDirection="row"
                width="100%"
                height={1}
                backgroundColor={isSel() ? palette.bgHover : undefined}
                onMouseDown={() => moveHighlight(row.globalIndex)}
              >
                <text fg={isSel() ? palette.fgBright : palette.fg}>
                  {`${isSel() ? "▸" : " "} ${row.item.label}`}
                </text>
              </box>
            );
          }}
        </For>
      </box>
      <text fg={palette.fgDim} height={1}>
        {`${effectiveOffset() + 1}–${Math.min(effectiveOffset() + visibleRows(), rows().length)} of ${rows().length} · ↑↓ scroll · PgUp/PgDn · Home/End · Enter select · Esc cancel`}
      </text>
      <KeyTrap focused={true} captureText onKeyDown={handleKey} />
    </Floating>
  );
}
