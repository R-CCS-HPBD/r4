/**
 * Fuzzy command palette.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import fuzzysort from "fuzzysort";
import { actions, workspace } from "../stores";
import { palette } from "../themes";
import { getCommands, runCommand } from "../commands";
import { loadSlashCatalog, localSlashCommands } from "../commands/slash";
import { buildSlashPaletteCommands, paletteEnterAction, paletteQueryMode, paletteTypedLine } from "../lib/slash-palette";
import { adjustListWindow } from "../lib/list-window";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import type { KeyEvent } from "@opentui/core";
import { Floating } from "../ui/Floating";
import { useTerminalDimensions } from "@opentui/solid";

export function formatPaletteLine(shortcut: string | undefined, title: string, namespace: string): string {
  const sc = shortcut ? `[${shortcut}]` : "   ";
  // Overlay is 50% of the terminal — the cap tracks it (was fixed 56).
  return truncate(`${sc} ${title}  ${namespace}`, Math.max(56, Math.floor(termWidth() * 0.5) - 8));
}

// HEIGHT BUDGET (keep in sync with the outer box below): Yoga sizes boxes
// border-inclusive, so maxHeight must cover border 2 + padding 2 +
// input row (1 + margin 1) + footer row 1 = 7 chrome rows plus the list
// rows (rowCap). Content beyond maxHeight is clipped by overflow="hidden" —
// footer/last rows vanish with no margin to the border (follow-up user
// report). rowCap = maxHeight - CHROME keeps the footer row ALWAYS inside
// the box.
const PALETTE_MAX_HEIGHT = 24;
const PALETTE_CHROME_ROWS = 7;
/** Rows below the card kept clear: the status bar and one spare row. */
const PALETTE_BOTTOM_CLEAR = 2;
const PALETTE_MIN_ROWS = 1;

/**
 * The card's height and list rows for a terminal `termRows` tall. The card
 * sits at top 20%, so a fixed 24-row card ran past the bottom of terminals
 * under ~29 rows: it painted over the status bar, hid its range footer and
 * let Enter run a selected row nobody could see (80x24, 60x20 —
 * stabilization 2026-10-21, resize-render#5).
 */
export function paletteHeightBudget(termRows: number): { maxHeight: number; rowCap: number } {
  const room = termRows - Math.floor(termRows * 0.2) - PALETTE_BOTTOM_CLEAR;
  const maxHeight = Math.max(PALETTE_CHROME_ROWS + PALETTE_MIN_ROWS, Math.min(PALETTE_MAX_HEIGHT, room));
  return { maxHeight, rowCap: maxHeight - PALETTE_CHROME_ROWS };
}

type NavStep = (selected: number, rowCap: number, last: number) => number;

/** Palette navigation keys (`C-x` = Ctrl+x); a Ctrl chord with no entry of
 * its own falls back to the plain key (Ctrl+Down moves like Down). */
const NAV_KEYS: Record<string, NavStep> = {
  down: (s) => s + 1,
  "C-n": (s) => s + 1,
  up: (s) => s - 1,
  "C-p": (s) => s - 1,
  pageup: (s, cap) => s - cap,
  "C-b": (s, cap) => s - cap,
  pagedown: (s, cap) => s + cap,
  "C-f": (s, cap) => s + cap,
  home: () => 0,
  "C-a": () => 0,
  end: (_s, _cap, last) => last,
  "C-e": (_s, _cap, last) => last,
};

function navStepFor(event: KeyEvent): NavStep | undefined {
  return (event.ctrl ? NAV_KEYS[`C-${event.name}`] : undefined) ?? NAV_KEYS[event.name];
}

export function CommandPaletteOverlay() {
  // V4 S2: single command surface — seed carries "/"/prefix when opened from
  // chat input or by a view (workflows pane seeds research verbs). Sync reactively
  // (mount timing vs seed-write ordering must not matter).
  const [query, setQuery] = createSignal("");
  // The merged catalog (local rows + the companion's list_slash_commands),
  // as the slash bar uses: with local rows only, a companion-only command
  // (/export, /artifact, /run, …) typed here ended in "No commands match."
  // (pty journey pin, fixed 2026-10-18). Local rows show until it loads.
  const [slashEntries, setSlashEntries] = createSignal(localSlashCommands());
  onMount(() => {
    void loadSlashCatalog().then((entries) => setSlashEntries(entries));
  });
  const [selected, setSelected] = createSignal(0);
  createEffect(() => {
    const seed = workspace.commandPaletteSeed;
    if (seed !== null) {
      setQuery(seed);
      setSelected(0);
    }
  });

  const results = createMemo(() => {
    // D2: slash verbs folded in; a leading `!` in the query reveals hidden ones.
    const mode = paletteQueryMode(query());
    const slash = buildSlashPaletteCommands(
      slashEntries(),
      { dispatch: (line) => actions.dispatchSlashFromChat(line), insertDraft: (text) => actions.setPendingChatDraft(text) },
      { includeHidden: mode.includeHidden },
    );
    const commands = [...getCommands(), ...slash];
    if (!mode.search) return commands;
    return fuzzysort
      .go(mode.search, commands, { keys: ["title", (c) => c.id.replace(":", " ")] })
      .map((r) => r.obj);
  });

  const safeSelected = createMemo(() => {
    const max = Math.max(0, results().length - 1);
    return Math.min(selected(), max);
  });

  // Bounded scrolling window: rows beyond the panel height were clipped AND
  // unreachable — the selection walked into invisible rows (user report).
  // Edge-follow window keeps the selected row on screen; footer shows range.
  // The card's height follows the terminal (paletteHeightBudget).
  const dims = useTerminalDimensions();
  const budget = createMemo(() => paletteHeightBudget(dims().height || 40));
  const rowCap = () => budget().rowCap;
  const [offset, setOffset] = createSignal(0);
  const win = createMemo(() =>
    adjustListWindow(results().length, safeSelected(), offset(), rowCap()),
  );
  const shown = createMemo(() => results().slice(win().start, win().end));

  /** Move selection and persist the window offset that keeps it visible. */
  const moveSelection = (idx: number) => {
    const next = Math.min(Math.max(idx, 0), Math.max(0, results().length - 1));
    setSelected(next);
    setOffset(adjustListWindow(results().length, next, offset(), rowCap()).offset);
  };

  const close = () => {
    actions.closeCommandPalette();
    setQuery("");
    setSelected(0);
    setOffset(0);
  };

  const runSelection = async (id: string) => {
    // Slash palette entries carry their own run closure (registry is keymap-only).
    if (id.startsWith("slash:")) {
      const cmd = results().find((c) => c.id === id);
      // BUG FIX: a typed line like "mode chat" must dispatch "/mode chat" —
      // the selected row's no-arg run() would silently drop the args.
      const typed = cmd ? paletteTypedLine(query(), cmd.id.slice(6)) : null;
      if (typed) {
        void actions.dispatchSlashFromChat(typed);
      } else if (cmd) {
        void cmd.run();
      }
      close();
      return;
    }
    const result = await runCommand(id);
    if (result.kind === "error") {
      actions.sendSystemNotice(result.message);
    }
    close();
  };

  /** Enter: the typed command as typed, else the selected row (paletteEnterAction). */
  const runEnter = () => {
    const rows = results();
    const action = paletteEnterAction(query(), slashEntries(), rows.map((c) => c.id), rows[safeSelected()]?.id);
    if (action && "line" in action) {
      void actions.dispatchSlashFromChat(action.line);
      close();
    } else if (action) {
      void runSelection(action.id);
    }
  };

  const handleKey = (event: KeyEvent) => {
    const step = navStepFor(event);
    if (step) {
      event.preventDefault();
      moveSelection(step(safeSelected(), rowCap(), results().length - 1));
    } else if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      runEnter();
    } else if (event.name === "escape") {
      event.preventDefault();
      close();
    }
  };

  return (
    <Floating
      position="absolute"
      width="50%"
      minHeight={Math.min(10, budget().maxHeight)}
      maxHeight={budget().maxHeight}
      left="25%"
      top="20%"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <box flexDirection="row" gap={1} alignItems="center" height={1} marginBottom={1} overflow="hidden">
        <text fg={palette.accent}>›</text>
        <input
          placeholder="Type a command..."
          value={query()}
          width="100%"
          focused={true}
          onInput={(value: string) => {
            setQuery(value);
            setSelected(0);
            setOffset(0);
          }}
          onKeyDown={handleKey}
          backgroundColor={palette.msgUserBg}
          textColor={palette.fgBright}
          cursorColor={palette.accent}
        />
      </box>
      <box flexDirection="column" overflow="hidden" flexGrow={1}>
        <For each={shown()}>
          {(cmd, idx) => {
            const isSel = () => win().start + idx() === safeSelected();
            return (
              <box
                width="100%"
                height={1}
                overflow="hidden"
                backgroundColor={isSel() ? palette.bgHover : undefined}
                onMouseDown={() => void runSelection(cmd.id)}
              >
                <text fg={isSel() ? palette.fgBright : palette.fg}>
                  {formatPaletteLine(cmd.shortcut, cmd.title, cmd.namespace)}
                </text>
              </box>
            );
          }}
        </For>
        <Show when={results().length === 0}>
          <box height={1} overflow="hidden">
            <text fg={palette.fgDim}>No commands match.</text>
          </box>
        </Show>
      </box>
      {/* Window range + scroll grammar (slash-picker parity). */}
      <Show when={results().length > 0}>
        <box height={1} overflow="hidden">
          <text fg={palette.fgDim}>
            {`${win().start + 1}–${win().end} of ${results().length} · ↑↓ scroll · PgUp/PgDn · Home/End · Enter run · Esc close`}
          </text>
        </box>
      </Show>
    </Floating>
  );
}
