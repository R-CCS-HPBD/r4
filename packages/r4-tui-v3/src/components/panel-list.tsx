/**
 * Scrollable flat-row list for narrower side panels — the models and skills
 * panels share this instead of their bespoke For blocks. Pure UI (rows
 * provided by the parent); keys given to the overlay keymap arrive bundled.
 */

import { For, Show, createMemo, createSignal } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { KeyTrap } from "./key-trap";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

export interface PanelRowItem {
  key: string;
  /** Text rendered after the badge. */
  label: string;
  /** Leading glyph/badge text (e.g. '-'; '>' for headers). */
  badge?: string;
  fg?: string;
  /** Optional suffix (e.g. '▾/▸' fold indicator or availability marker). */
  suffix?: string;
  onSelect?: () => void;
}

export interface PanelSection {
  /** Section header label (rendered darker, not selectable). */
  title: string;
  /** Collapsed when true — items hidden + badge flips. */
  collapsed: boolean;
  onToggle: () => void;
  items: PanelRowItem[];
}

/** A host pane's handle on an embedded list (keyboard={false}). */
export interface PanelListScroll {
  /** One page down (+1) or up (-1). */
  page(direction: 1 | -1): void;
}

/** Keep total height bounded; scroll with ↑↓ + j/k within the visible window. */
export function PanelList(props: {
  sections: PanelSection[];
  maxRows: number;
  onClose?: () => void;
  /** False: no key trap of its own. A pane whose trap owns ↑↓
   * (Infrastructure) embeds the list this way and scrolls it through
   * `onScrollApi` — one key owner (TUI audit r2 #15). Default true. */
  keyboard?: boolean;
  /** Receives the scroll handle the host pane's trap drives (PgUp/PgDn). */
  onScrollApi?: (api: PanelListScroll) => void;
  /** Tab while this list owns the keys: the host's panel cycle (Settings,
   * issue #217). Absent: Tab passes through. */
  onTab?: () => void;
}) {
  const windowRows = createMemo(() => {
    // Flatten sections with their collapsible state into a single visible list.
    const flat: Array<{ kind: "header"; section: PanelSection } | { kind: "item"; item: PanelRowItem }> = [];
    for (const section of props.sections) {
      flat.push({ kind: "header", section });
      if (!section.collapsed) {
        for (const item of section.items) flat.push({ kind: "item", item });
      }
    }
    return flat;
  });

  const [offset, setOffset] = createSignal(0);
  const [cursor, setCursor] = createSignal(0);
  const itemCount = () => windowRows().length;
  const visibleCount = () => Math.min(props.maxRows, itemCount());
  const startIndex = () => {
    const max = Math.max(0, itemCount() - visibleCount());
    return Math.min(offset(), max);
  };
  const shownRows = () => windowRows().slice(startIndex(), startIndex() + visibleCount());
  const selected = (i: number) => cursor() === i;

  const moveCursor = (delta: number) => {
    const next = Math.min(Math.max(cursor() + delta, 0), Math.max(0, itemCount() - 1));
    setCursor(next);
    // keep selection visible
    if (next < startIndex()) setOffset(next);
    else if (next >= startIndex() + visibleCount()) setOffset(next - visibleCount() + 1);
  };

  const moves = new Map<string, () => number>([
    ["up", () => -1],
    ["k", () => -1],
    ["down", () => 1],
    ["j", () => 1],
    ["pagedown", () => visibleCount()],
    ["pageup", () => -visibleCount()],
  ]);

  const selectUnderCursor = (event: KeyEvent) => {
    const row = shownRows()[cursor() - startIndex()];
    if (row?.kind !== "item" || (event.name !== "enter" && event.name !== "return")) return;
    row.item.onSelect?.();
    event.preventDefault();
  };

  props.onScrollApi?.({ page: (direction) => moveCursor(direction * visibleCount()) });

  const footerHint = (): string => {
    const more = itemCount() - visibleCount();
    if (props.keyboard === false) return more > 0 ? `PgUp/PgDn scroll · ${more} more rows` : "";
    if (more > 0) return `↑↓ / j,k scroll · ${more} more rows`;
    return props.onClose ? "Esc close · Enter select" : "↑↓ scroll · Enter select";
  };

  const onKey = (event: KeyEvent) => {
    const move = moves.get(event.name ?? "");
    if (move) {
      moveCursor(move());
      event.preventDefault();
      return;
    }
    if (event.name === "escape" && props.onClose) {
      props.onClose();
      event.preventDefault();
      return;
    }
    if (event.name === "tab" && props.onTab) {
      props.onTab();
      event.preventDefault();
      return;
    }
    selectUnderCursor(event);
  };

  return (
    <box flexDirection="column" width="100%">
      <For each={shownRows()}>
        {(row, i) => {
          const index = () => startIndex() + i();
          if (row.kind === "header") {
            return (
              <box
                height={1}
                width="100%"
                backgroundColor={palette.bgPanel}
                onMouseDown={row.section.onToggle}
              >
                <text fg={palette.fgDim}>
                  {truncate(`${row.section.collapsed ? "▸" : "▾"} ${row.section.title}`, panePanelWidth(1, 8))}
                </text>
              </box>
            );
          }
          const item = row.item;
          return (
            <box
              height={1}
              width="100%"
              backgroundColor={selected(index()) ? palette.bgActive : undefined}
              onMouseDown={() => {
                setCursor(index());
                item.onSelect?.();
              }}
            >
              <text fg={item.fg ?? (selected(index()) ? palette.fgBright : palette.fg)}>
                {truncate(
                  `${item.badge ?? " "}${item.label}${item.suffix ? ` ${item.suffix}` : ""}`,
                  props.maxRows > 0 ? 38 : 76,
                )}
              </text>
            </box>
          );
        }}
      </For>
      <box height={1} width="100%" overflow="hidden" marginTop={1}>
        <text fg={palette.fgDim}>
          {footerHint()}
        </text>
      </box>
      <Show when={props.keyboard !== false}>
        <box height={1} width={1} overflow="hidden">
          <KeyTrap paneScoped focused onKeyDown={onKey} />
        </box>
      </Show>
    </box>
  );
}
