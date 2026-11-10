/**
 * Workflow-definition card: Enter on a work-pane `wf:` row opens the
 * registered workflow's package (2026-10-01), and since 2026-10-02 reads as
 * the instructions for running it —
 *   meta · how to run (the exact `/workflow trigger` line) · parameters
 *   (type, default, description, where each is read) · graph (boxes and
 *   arrows of the steps the runner executes) · steps · code
 * One scrolling body (↑↓ PgUp PgDn Home End, mouse wheel); code files stay
 * collapsed until Enter/click (←→ pick the file). `r` puts the trigger line
 * in the composer and closes the card and pane so the composer has the keys;
 * `e` (2026-11-07) saves the workflow as a verified zip (lib/workflow-export.ts);
 * `v` / `V` (ADR-113 WV4, 2026-11-09) step to the next / previous registered
 * version (the overlay id becomes `name@version`, and `r` pins that version).
 * Esc closes via the global overlay sweep. Capability-detected: slots with
 * no data stay hidden; a backend without the route shows the honest error.
 */

import { For, Show, createEffect, createMemo, createSignal, on } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import type { KeyEvent, ScrollBoxRenderable } from "@opentui/core";
import { workspace, actions } from "../stores";
import { umrsWorkflowActions, umrsWorkflowStore } from "../stores/umrs-workflow";
import { cardTriggerCommand, cardVersion, workflowCardRows, type CardTone } from "../lib/workflow-card-rows";
import { cycleVersion, workflowRefLabel } from "../lib/workflow-versions";
import { isSharedInCatalog } from "../lib/workflow-sharing";
import { exportWorkflowWithNotice } from "../lib/workflow-export";
import { palette } from "../themes";
import { Floating } from "../ui/Floating";
import { KeyTrap } from "./key-trap";

/** Card width as a share of the terminal (matches `width` below). */
const CARD_SHARE = 0.82;
/** Horizontal padding inside the card (both sides). */
const CARD_CHROME = 4;

function toneFg(tone: CardTone): string {
  if (tone === "accent") return palette.accent2;
  if (tone === "dim") return palette.fgDim;
  if (tone === "bright") return palette.fgBright;
  return palette.fg;
}

type KeyActions = Record<string, () => void>;

/** Own-key lookup: a key name never resolves to an Object.prototype member. */
function ownAction(table: KeyActions, key: string): (() => void) | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined;
}

/** Put a workflow's trigger line in the composer and hand it the keyboard. */
export function draftWorkflowTrigger(command: string, required: number): void {
  actions.setPendingChatDraft(command);
  actions.setWorkflowDefId(null);
  actions.closePane();
  actions.sendSystemNotice(
    required
      ? `composer: ${command} — fill each value after "=", then Enter`
      : `composer: ${command} — Enter runs it`,
  );
}

export function WorkflowDefOverlay() {
  const name = () => workspace.workflowDefId;
  const dims = useTerminalDims();
  const [cursor, setCursor] = createSignal(0);
  /** Expanded code file path (null = everything collapsed). */
  const [expanded, setExpanded] = createSignal<string | null>(null);

  const detail = createMemo(() => {
    const id = name();
    return id ? umrsWorkflowStore.definitions[id] : undefined;
  });
  const error = createMemo(() => {
    const id = name();
    return id ? umrsWorkflowStore.definitionErrors[id] : undefined;
  });

  /** `name@version` once the package says which version it is (ADR-113). */
  const title = createMemo(() => {
    const d = detail();
    return d && cardVersion(d) ? workflowRefLabel(d.workflow_name, d.version) : (name() ?? "");
  });

  let scrollRef: ScrollBoxRenderable | undefined;
  const scrollTo = (y: number): void => {
    if (!scrollRef) return;
    // Rows are one cell high, so the content height is known before layout
    // catches up: an opened file is scrolled to in the same keystroke.
    const height = rows().length;
    scrollRef.verticalScrollBar.scrollSize = height;
    const max = Math.max(0, height - scrollRef.height);
    scrollRef.scrollTo({ x: 0, y: Math.max(0, Math.min(max, y)) });
  };

  const files = createMemo(() => detail()?.files ?? []);
  const safeCursor = createMemo(() => Math.min(cursor(), Math.max(0, files().length - 1)));
  const rows = createMemo(() => {
    const d = detail();
    if (!d) return [];
    const width = Math.max(40, Math.floor(dims().width * CARD_SHARE) - CARD_CHROME);
    const shared = isSharedInCatalog(umrsWorkflowStore.catalog, d.workflow_name, d.user_id);
    return workflowCardRows(shared ? { ...d, shared } : d, { width, expanded: expanded(), cursor: safeCursor() });
  });

  // A different workflow starts collapsed at the top.
  createEffect(
    on(name, () => {
      setCursor(0);
      setExpanded(null);
      scrollTo(0);
    }),
  );

  /** Scroll just enough that the file row under the cursor is visible (rows are 1 high). */
  const revealFile = (path: string) => {
    if (!scrollRef) return;
    const index = rows().findIndex((r) => r.file === path);
    if (index < 0) return;
    if (index < scrollRef.scrollTop) scrollTo(index);
    else if (index >= scrollRef.scrollTop + scrollRef.height) scrollTo(index - scrollRef.height + 1);
  };

  const toggle = (path: string) => {
    const opening = expanded() !== path;
    setExpanded(opening ? path : null);
    const index = files().findIndex((f) => f.path === path);
    if (index >= 0) setCursor(index);
    // An opened file's header goes to the top (as far as the scroll range allows).
    if (opening) scrollTo(rows().findIndex((r) => r.file === path));
  };

  const draftTrigger = () => {
    const d = detail();
    if (!d) return;
    const { command, required } = cardTriggerCommand(d);
    draftWorkflowTrigger(command, required);
  };

  const scrollKey = (key: string): boolean => {
    if (!scrollRef) return false;
    const page = Math.max(1, scrollRef.height - 1);
    const top = scrollRef.scrollTop;
    const target: Record<string, number> = {
      up: top - 1,
      down: top + 1,
      pageup: top - page,
      pagedown: top + page,
      home: 0,
      end: scrollRef.scrollHeight,
    };
    if (!(key in target)) return false;
    scrollTo(target[key]!);
    return true;
  };

  /** `v` / `V` (ADR-113 WV4): show the next / previous registered version;
   * the card re-reads that version's package and `r` then pins it. */
  const pickVersion = (step: number) => {
    const d = detail();
    const next = d ? cycleVersion(d.versions ?? [], d.version, step) : undefined;
    if (!d || !next) return;
    const ref = workflowRefLabel(d.workflow_name, next);
    actions.setWorkflowDefId(ref);
    if (!umrsWorkflowStore.definitions[ref]) void umrsWorkflowActions.fetchDefinition(ref).catch(() => undefined);
  };

  /** `e`: save this workflow as a verified zip (cwd, else ~/Downloads); the card stays open. */
  const exportDefinition = () => {
    const id = name();
    if (id) void exportWorkflowWithNotice(id);
  };

  const moveFile = (step: number) => {
    const last = Math.max(0, files().length - 1);
    setCursor(Math.max(0, Math.min(last, safeCursor() + step)));
    const f = files()[safeCursor()];
    if (f) revealFile(f.path);
  };

  const toggleSelectedFile = () => {
    const f = files()[safeCursor()];
    if (f) toggle(f.path);
  };

  /** File keys the card owns besides scrolling (with or without modifiers). */
  const fileKeys: KeyActions = {
    left: () => moveFile(-1),
    right: () => moveFile(1),
    return: toggleSelectedFile,
    enter: toggleSelectedFile,
  };
  /** Letter keys: only without Ctrl/Meta (those chords belong to the app). */
  const letterKeys: KeyActions = {
    r: draftTrigger,
    e: exportDefinition,
    v: () => pickVersion(1),
    V: () => pickVersion(-1),
  };

  const keyAction = (event: KeyEvent, key: string): (() => void) | undefined => {
    const plain = !event.ctrl && !event.meta;
    if (!plain) return ownAction(fileKeys, key);
    // Shift picks the upper-case verb (V) where one exists; Shift+r stays r.
    const upper = event.shift ? ownAction(letterKeys, key.toUpperCase()) : undefined;
    return ownAction(fileKeys, key) ?? upper ?? ownAction(letterKeys, key);
  };

  const handleKey = (event: KeyEvent) => {
    const key = event.name ?? "";
    const action = keyAction(event, key);
    if (action) action();
    if (action || scrollKey(key)) event.preventDefault();
  };

  return (
    <Show when={name()}>
      <Floating
        position="absolute"
        width="82%"
        left="9%"
        top="6%"
        height="88%"
        flexDirection="column"
        padding={2}
      >
        <box height={1} flexShrink={0} overflow="hidden">
          <text fg={palette.accent}>{`workflow definition — ${title()}`}</text>
        </box>
        <Show when={error()}>
          <box height={1} marginTop={1} flexShrink={0} overflow="hidden">
            <text fg={palette.fgBright}>
              {`package unavailable: ${error()} — backend route may predate 2026-10-01`}
            </text>
          </box>
        </Show>
        <Show when={!error() && detail() === undefined}>
          <box height={1} marginTop={1} flexShrink={0}>
            <text fg={palette.fgDim}>loading…</text>
          </box>
        </Show>
        <scrollbox
          ref={(el: ScrollBoxRenderable) => {
            scrollRef = el;
          }}
          flexGrow={1}
          minHeight={0}
          marginTop={1}
          width="100%"
          scrollY
          overflow="hidden"
        >
          <For each={rows()}>
            {(row) => {
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={row.selected ? palette.bgHover : undefined}
                  onMouseDown={row.file !== undefined ? () => toggle(row.file!) : undefined}
                >
                  <text fg={toneFg(row.tone)}>{row.text}</text>
                </box>
              );
            }}
          </For>
        </scrollbox>
        <box height={1} marginTop={1} flexShrink={0} overflow="hidden">
          <text fg={palette.fgDim}>
            {`r trigger line → composer · ${(detail()?.versions?.length ?? 0) > 1 ? "v/V version · " : ""}e export zip · ↑↓ PgUp PgDn scroll · ←→ file · Enter/click code · Esc close`}
          </text>
        </box>
        {/* captureText: letters other than `r`/`e` never reach space shortcuts under the card. */}
        <KeyTrap focused={true} captureText={true} onKeyDown={handleKey} />
      </Floating>
    </Show>
  );
}
