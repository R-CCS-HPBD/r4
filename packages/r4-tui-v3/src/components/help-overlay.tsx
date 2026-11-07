/**
 * F12 / Ctrl+Shift+H / /help help overlay (L3 wave).
 *
 * ONE canonical reference surface. Every other help-ish line (status-bar
 * corner hint, settings stubs) points here — the copy NEVER drifts because
 * the slash catalog re-derives from the live command table, the chord legend
 * is the only one it ships, and the live keymap view comes straight out of
 * the registered bindings.
 *
 * FULL help, nothing truncated: the catalog renders one row per command
 * (with help text + subcommand rows) inside a scrollbox — the first draft
 * sliced each category row at 86 chars and capped the keymap at 14 rows, so
 * half the verbs (/usage, /workflows, /loops, /rag…) never appeared and the
 * unbounded box clipped whatever the terminal bottom cut off. Rows stay
 * height-1 boxed so nothing collapses into an overlapping layout; vertical
 * room comes from scrolling (↑↓/PgUp/PgDn/Home/End/wheel), not from hiding.
 */

import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import type { KeyEvent, ScrollBoxRenderable } from "@opentui/core";
import { actions, workspace } from "../stores";
import { palette } from "../themes";
import {
  PROJECT_SUBCOMMANDS,
  loadSlashCatalog,
  localSlashCommands,
  type SlashCommandEntry,
} from "../commands/slash";
import { getRegisteredBindings } from "../keymap";
import { groupBindings, whichKeyRows } from "../lib/which-key";
import type { WhichKeyInput } from "../lib/which-key";
import { buildHelpRows, type HelpRow } from "../lib/help-rows";
import { KeyTrap } from "./key-trap";
import { Floating } from "../ui/Floating";

function OneLine(props: { fg?: string; children: string }) {
  return (
    <box height={1} overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function rowFg(tone: HelpRow["tone"]): string {
  if (tone === "accent") return palette.accent;
  if (tone === "dim") return palette.fgDim;
  return palette.fg;
}

function HelpOverlayInner() {
  // Local table first (synchronous); upgrade to the merged local+companion
  // catalog when the bridge answers. Offline/test rigs stay on the local
  // table — loadSlashCatalog falls back to it on failure, never rejects
  // into the overlay.
  const [entries, setEntries] = createSignal<SlashCommandEntry[]>(localSlashCommands());
  onMount(() => {
    void loadSlashCatalog()
      .then((list) => {
        if (list.length > 0) setEntries(list);
      })
      .catch(() => {});
  });

  // Full keymap — no per-group cap, no 14-row slice: the scrollbox makes
  // every registered binding reachable.
  const keyRows = createMemo(() =>
    whichKeyRows(
      groupBindings(getRegisteredBindings() as WhichKeyInput[]),
      Number.MAX_SAFE_INTEGER,
    ),
  );

  const rows = createMemo(() =>
    buildHelpRows({
      entries: entries(),
      keyRows: keyRows(),
      projectSubs: PROJECT_SUBCOMMANDS,
    }),
  );

  let scrollRef: ScrollBoxRenderable | undefined;
  const scrollTo = (y: number) => {
    if (!scrollRef) return;
    const max = Math.max(0, scrollRef.scrollHeight - scrollRef.height);
    scrollRef.scrollTo({ x: 0, y: Math.max(0, Math.min(max, y)) });
  };
  /** Scroll target per key, from the current offset and page height. */
  const SCROLL_KEYS: Record<string, (top: number, page: number, full: number) => number> = {
    up: (top) => top - 1,
    down: (top) => top + 1,
    pageup: (top, page) => top - page,
    pagedown: (top, page) => top + page,
    home: () => 0,
    end: (_top, _page, full) => full,
  };
  const onKey = (event: KeyEvent) => {
    const name = event.name ?? "";
    if (name === "escape") {
      event.preventDefault();
      // Help closes alone: never the running turn or the pane underneath.
      actions.closeModalOverlays();
      return;
    }
    const target = SCROLL_KEYS[name];
    if (!scrollRef || !target) return;
    event.preventDefault();
    scrollTo(target(scrollRef.scrollTop, Math.max(1, scrollRef.height - 1), scrollRef.scrollHeight));
  };

  return (
    <Floating
      position="absolute"
      width="86%"
      left="7%"
      top="6%"
      height="88%"
      title="help"
      flexDirection="column"
      padding={1}
    >
      <scrollbox
        ref={(el: ScrollBoxRenderable) => {
          scrollRef = el;
        }}
        flexGrow={1}
        minHeight={0}
        width="100%"
        scrollY
        stickyScroll
        stickyStart="top"
        overflow="hidden"
      >
        <For each={rows()}>
          {(row) => <OneLine fg={rowFg(row.tone)}>{row.text}</OneLine>}
        </For>
      </scrollbox>
      <box height={1} width="100%" marginTop={1} overflow="hidden">
        <text fg={palette.fgDim}>
          ↑↓ scroll · PgUp/PgDn page · Home/End jump · wheel scrolls · Esc close
        </text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap focused onKeyDown={onKey} />
      </box>
    </Floating>
  );
}

export function HelpOverlay() {
  return (
    <Show when={workspace.helpOpen}>
      <HelpOverlayInner />
    </Show>
  );
}
