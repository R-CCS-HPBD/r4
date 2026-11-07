/**
 * Chat tab strip — opencode-v2-style parallel session tabs (2026-09-29).
 *
 * One row above the chat frame. Each tab: ⓪ ordinal + title + status glyph:
 *   ● busy (turn in flight, works in background)
 *   ! attention (approval/question/error landed while you were elsewhere)
 *   +N absorbed background events (informational badge, cleared on visit)
 * The title marquee-scrolls (ping-pong with dwell at both ends) when it does
 * not fit the tab's budget — only the ACTIVE tab animates; background tabs
 * ellipsize so the whole strip stays still except the cell you are looking
 * at. Titles follow the SERVER session name (fetched lazily; `chat N` is the
 * fallback for sessions without one yet).
 *
 * Mouse: click activates; click the × closes. Keys: Ctrl+N new, Alt+←/→
 * cycle, Ctrl+Alt+W close, Ctrl+Shift+T reopen (help overlay lists them).
 */

import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { actions, workspace } from "../stores";
import { palette } from "../themes";
import { useTerminalDims } from "../lib/terminal-dims";
import { chatColumnWidth } from "../lib/term-width";
import { paneBounds } from "../lib/pane-measure";
import { tabTitle } from "../tabs/model";
import { ellipsisSlice, marqueeSlice } from "../lib/marquee";
import { displayWidth } from "../lib/markdown-wrap";
import type { ChatTabMeta } from "../tabs/model";

const MARQUEE_TICK_MS = 250;
/** opencode's 224px ≈ 28 columns: beyond that, long titles marquee instead
 *  of letting one chat hog the strip. */
const TAB_CELL_MAX_COLS = 28;

function tabGlyph(tab: ChatTabMeta): { glyph: string; fg: string } {
  if (tab.needsAttention) return { glyph: " !", fg: palette.statusWarn };
  if (tab.busy) return { glyph: " ●", fg: palette.statusOk };
  return { glyph: "", fg: palette.fgDim };
}

/** Title budget inside a cell: ordinal + glyph + badge + paddings are chrome.
 *  Shared by the cell render AND the strip's marquee gate — one formula, no
 *  off-by-one drift between "should scroll" and "what actually shows". */
export function tabTitleBudget(cap: number, ordinal: number, tab: ChatTabMeta): number {
  const glyphLen = tab.needsAttention || tab.busy ? 2 : 0;
  const badgeLen = tab.absorbed > 0 ? String(tab.absorbed).length + 1 : 0;
  // Floor 1, not 4: a 4-column floor outgrew narrow cells (8 tabs at 70
  // columns) and the overflow clipped the × of every other cell.
  return Math.max(1, cap - String(ordinal).length - 1 - glyphLen - badgeLen - 1);
}

function TabCell(props: {
  tab: ChatTabMeta;
  ordinal: number;
  active: boolean;
  cap: number;
  tickMs: number;
}) {
  const glyph = () => tabGlyph(props.tab);
  const absorbed = () => (props.tab.absorbed > 0 ? `+${props.tab.absorbed}` : "");
  /** `N:` prefix + glyph + badge are fixed chrome; title scrolls inside the rest. */
  const titleCap = () => tabTitleBudget(props.cap, props.ordinal, props.tab);
  const titleText = () => tabTitle(props.tab, props.ordinal);
  const title = () => {
    const raw = titleText();
    return props.active
      ? marqueeSlice(raw, titleCap(), props.tickMs)
      : ellipsisSlice(raw, titleCap());
  };
  return (
    <box
      height={1}
      maxWidth={props.cap + 2}
      paddingLeft={1}
      backgroundColor={props.active ? palette.bgActive : undefined}
      onMouseDown={() => actions.tabActivate(props.tab.id)}
      flexDirection="row"
      flexShrink={1}
      overflow="hidden"
    >
      {/* Only the title may shrink: a squeezed row clipped the `N:` colon
          and the clickable × instead (stabilization 2026-10-21). */}
      <text flexShrink={0} fg={props.active ? palette.accent : palette.fgDim}>{`${props.ordinal}:`}</text>
      <text fg={props.active ? palette.accent : glyph().fg}>{title()}</text>
      <Show when={glyph().glyph}>
        <text flexShrink={0} fg={glyph().fg}>{glyph().glyph}</text>
      </Show>
      <Show when={absorbed()}>
        <text flexShrink={0} fg={palette.fgDim}>{absorbed()}</text>
      </Show>
      <Show when={workspace.chatTabs.length > 1}>
        <box
          flexShrink={0}
          onMouseDown={(e) => {
            e.stopPropagation?.();
            void actions.tabClose(props.tab.id);
          }}
        >
          <text fg={props.active ? palette.statusErr : palette.fgDim}>×</text>
        </box>
      </Show>
    </box>
  );
}

/** Strip chrome outside the cells: paddingLeft/Right (2) + "[+ new]" (7) +
 *  one gap={1} per child after the first (the cells, the button, the spacer
 *  and, with one tab, the hint) + the hint text (34). */
function stripChrome(count: number): number {
  const hint = count <= 1 ? 34 + 1 : 0;
  return 2 + 7 + (count + 1) + hint;
}

/** Per-cell cap: every chrome column is priced in, so the row never
 *  overflows — it did by ~2 columns per tab (each cell's paddingLeft and
 *  gap were unpriced), and the squeeze clipped `N:` and × (2026-10-21). A
 *  cell renders cap + its paddingLeft. */
export function stripTabCap(col: number, tabCount: number): number {
  const count = Math.max(1, tabCount);
  const avail = Math.max(10, col - stripChrome(count));
  // Floor: `N:` + one title column + × always fit.
  return Math.max(4, Math.min(TAB_CELL_MAX_COLS, Math.floor(avail / count) - 1));
}

export function ChatTabStrip() {
  const dims = useTerminalDims();
  const [tickMs, setTickMs] = createSignal(0);
  /** Even budget per tab, hard-capped like opencode's strip; the pane-aware
   *  chat column keeps the math honest on narrow terminals. */
  const perTabCap = () =>
    stripTabCap(
      // Same column as the chat cells: a dragged pane (paneWidthOverride) and
      // the measured dock narrow it below the default estimate (w3-tui #2).
      chatColumnWidth(dims().width, workspace.paneView !== null, workspace.paneWidthOverride, paneBounds()),
      workspace.chatTabs.length,
    );
  /** Stillness rule: animate ONLY while the active title overflows its cell. */
  const activeOverflows = () => {
    const idx = workspace.chatTabs.findIndex((t) => t.id === workspace.activeChatTabId);
    if (idx < 0) return false;
    const tab = workspace.chatTabs[idx]!;
    return displayWidth(tabTitle(tab, idx + 1)) > tabTitleBudget(perTabCap(), idx + 1, tab);
  };
  createEffect(() => {
    if (!activeOverflows()) return;
    const id = setInterval(() => setTickMs((t) => t + MARQUEE_TICK_MS), MARQUEE_TICK_MS);
    onCleanup(() => clearInterval(id));
  });
  return (
    <box
      height={1}
      width="100%"
      flexDirection="row"
      overflow="hidden"
      gap={1}
      paddingLeft={1}
      paddingRight={1}
    >
      <For each={workspace.chatTabs}>
        {(tab, i) => (
          <TabCell
            tab={tab}
            ordinal={i() + 1}
            active={tab.id === workspace.activeChatTabId}
            cap={perTabCap()}
            tickMs={tickMs()}
          />
        )}
      </For>
      <box height={1} onMouseDown={() => actions.tabCreate()}>
        <text fg={palette.fgDim}>[+ new]</text>
      </box>
      <box flexGrow={1} />
      {/* Hint earns its cells only while it teaches (single tab); with 2+
          tabs the budget goes to titles and the row never overflows. */}
      <Show when={workspace.chatTabs.length <= 1}>
        <text fg={palette.fgDim}>{"1 tab · ctrl+n new · alt+←/→ switch"}</text>
      </Show>
    </box>
  );
}
