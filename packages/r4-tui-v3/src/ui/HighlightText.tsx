/**
 * One colorized text line: semantic spans from lib/text-highlight rendered
 * as sibling <text> nodes (same pattern as CodeLine). baseFg is the fallback
 * for plain (uncolored) runs.
 *
 * Every span is flexShrink=0 + wrapMode="none" (stabilization 2026-10-21):
 * with Yoga's default shrink, a row wider than its box shrank EACH span in
 * proportion — each lost its own tail (wrapped onto a hidden second row),
 * so words vanished mid-line. Now an overwide row clips at the right edge
 * only; callers pre-wrap prose (MarkdownBlock wrapWidth) or cap the row.
 */

import { For } from "solid-js";
import { highlightLine } from "../lib/text-highlight";
import { terminalSafe } from "../lib/terminal-safe";

export function HighlightText(props: { text: string; baseFg?: string }) {
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden">
      <For each={highlightLine(terminalSafe(props.text))}>
        {(span) => (
          <text fg={span.fg ?? props.baseFg} flexShrink={0} wrapMode="none">
            {span.text}
          </text>
        )}
      </For>
    </box>
  );
}
