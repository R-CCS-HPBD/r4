/**
 * Citation chip → passage preview (knowledge plan K1.4). Opened with `o` on
 * a reply that lists sources; ←/→ or [ ] step through them; ↑/↓ and
 * PgUp/PgDn scroll a long passage; Esc/q closes.
 * The passage comes from `citation.open` (lib/citation-preview.ts).
 *
 * Review of K1 part A (2026-10-24): the body was wrapped at a fixed 96
 * columns inside an overlay 80% of the terminal wide, so below ~124 columns
 * every line lost its tail; and it was cut at 16 rows with no way to read
 * on. It now wraps at word boundaries to the overlay's own width and
 * scrolls.
 */

import { For, createEffect, createMemo, createSignal, on } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { workspace } from "../stores";
import { palette } from "../themes";
import { KeyTrap } from "./key-trap";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import { wrapProseLine } from "../lib/markdown-wrap";
import { Floating } from "../ui/Floating";
import { closeCitationPreview, stepCitationPreview } from "../lib/citation-preview";
import type { CitationPreviewState } from "../types";

const BODY_ROWS = 16;
/** The overlay's share of the terminal, and its padding (one column each side). */
const WIDTH_SHARE = 0.8;
const PADDING_COLS = 2;
const MIN_COLS = 20;

/** Columns the body can paint at the current terminal width. */
export function previewWidth(cols = termWidth()): number {
  return Math.max(MIN_COLS, Math.floor(cols * WIDTH_SHARE) - PADDING_COLS);
}

/** The passage wrapped at word boundaries to `width` columns (every row). */
export function previewLines(text: string, width = previewWidth()): string[] {
  return text.split("\n").flatMap((line) => wrapProseLine(line, width));
}

/** The rows shown from `top`, at most `rows` of them. */
export function previewWindow(lines: readonly string[], top: number, rows = BODY_ROWS): string[] {
  return lines.slice(top, top + rows);
}

/** The scroll position after moving `delta` rows, kept on the passage. */
export function scrolledTop(top: number, delta: number, total: number, rows = BODY_ROWS): number {
  return Math.max(0, Math.min(top + delta, Math.max(0, total - rows)));
}

/** "Source 2/3 · [S4] paper.pdf · cited" (chunks when the server said). */
export function previewHeader(state: CitationPreviewState): string {
  const cite = state.citations[state.index];
  const kind = cite?.kind === "retrieved" ? "retrieved, not cited" : "cited";
  const chunks = state.chunks.length > 0 ? ` · chunks ${state.chunks[0]}–${state.chunks.at(-1)}` : "";
  return `Source ${state.index + 1}/${state.citations.length} · ${cite?.title ?? "?"} · ${kind}${chunks}`;
}

/** The footer: the keys, and where in a passage longer than the body. */
export function previewFooter(top: number, total: number, rows = BODY_ROWS): string {
  const keys = "←/→ or [ ] other sources · Esc close";
  if (total <= rows) return keys;
  return `↑/↓ PgUp/PgDn scroll · lines ${top + 1}–${Math.min(total, top + rows)} of ${total} · ${keys}`;
}

export function CitationPreviewOverlay() {
  const state = () => workspace.citationPreview;
  const [top, setTop] = createSignal(0);
  const lines = createMemo(() => {
    const current = state();
    if (!current) return [];
    if (current.text === null) return ["… opening the passage"];
    return previewLines(current.text || "(empty passage)");
  });
  // A new source (or its text arriving) starts at its first row.
  createEffect(on(() => [state()?.index, state()?.text], () => setTop(0)));

  const scroll = (delta: number) => setTop(scrolledTop(top(), delta, lines().length));
  const keys: Record<string, () => unknown> = {
    escape: () => closeCitationPreview(),
    q: () => closeCitationPreview(),
    "[": () => stepCitationPreview(-1),
    left: () => stepCitationPreview(-1),
    "]": () => stepCitationPreview(1),
    right: () => stepCitationPreview(1),
    up: () => scroll(-1),
    down: () => scroll(1),
    pageup: () => scroll(-(BODY_ROWS - 1)),
    pagedown: () => scroll(BODY_ROWS - 1),
  };

  const handleKey = (event: KeyEvent) => {
    const action = keys[event.name];
    if (!action) return;
    event.preventDefault();
    void action();
  };

  return (
    <Floating
      position="absolute"
      width="80%"
      height={BODY_ROWS + 7}
      left="10%"
      top="10%"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <text fg={palette.accent} height={1}>
        {truncate(state() ? previewHeader(state()!) : "", previewWidth())}
      </text>
      <text fg={palette.fgDim} height={1}>
        {truncate(state()?.note ?? "", previewWidth())}
      </text>
      <box flexDirection="column" height={BODY_ROWS} overflow="hidden">
        <For each={previewWindow(lines(), top())}>
          {(line) => (
            <box height={1} overflow="hidden">
              <text fg={palette.fg}>{line}</text>
            </box>
          )}
        </For>
      </box>
      <text fg={palette.fgDim} height={1}>
        {truncate(previewFooter(top(), lines().length), previewWidth())}
      </text>
      <KeyTrap focused={true} captureText onKeyDown={handleKey} />
    </Floating>
  );
}
