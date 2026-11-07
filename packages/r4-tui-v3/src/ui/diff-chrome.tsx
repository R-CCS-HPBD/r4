/**
 * Diff chrome — the opencode <diff> look for our renderers: tinted row
 * backgrounds (add/del/hunk), right-aligned line-number gutter, colored
 * sign column, shiki token spans with visible `…` truncation. Shared by
 * DiffBlock (unified) and ParallelDiffBlock (side-by-side).
 */

import { For, Show, createMemo } from "solid-js";
import { palette } from "../themes";
import { mixHex } from "../lib/logo-animation";
import type { ShikiSpan } from "../lib/code-shiki";

/** Row tints: 12% signal over the panel bg (add/del), 8% (hunk) — the
 * midpoint the maintainer named between 4% (too light, blocks vanished)
 * and the inverted ~96% (blinding). mixHex is (a,b,t) → b weighted by t,
 * so the SUBTLE form is mixHex(bg, fg, small). Computed per call —
 * palette is a live singleton, never frozen at module eval. */
export function diffRowBg(kind: "add" | "del" | "hunk"): string {
  const fg =
    kind === "add" ? palette.diffAdd : kind === "del" ? palette.diffDel : palette.diffHunk;
  return mixHex(palette.bgPanel, fg, kind === "hunk" ? 0.08 : 0.12);
}

export function diffSignFg(kind: "add" | "del"): string {
  return kind === "add" ? palette.diffAdd : palette.diffDel;
}

const GUTTER_COL = 4;

/** Right-aligned number cell (4 cols) or blank when the row carries none. */
export function numCell(no: number | null): string {
  return no === null ? " ".repeat(GUTTER_COL) : String(no).padStart(GUTTER_COL);
}

// Chrome texts are flexShrink=0 (stabilization 2026-10-21): when a row
// overflowed, Yoga shrank the gutter/sign texts too, so the `│` separator
// and the sign's space vanished ("1      - old", "│-old").

/** Line-number gutter prefix: ` old new │ `. */
export function Gutter(props: { leftNo: number | null; rightNo: number | null }) {
  return (
    <text fg={palette.fgDim} flexShrink={0} wrapMode="none">
      {`${numCell(props.leftNo)} ${numCell(props.rightNo)} │ `}
    </text>
  );
}

// 4 + 1 + 4 + " │ " = 12 (was 11: every unified body row overflowed by one
// column, which the engine resolved by shrinking the gutter).
export const GUTTER_WIDTH = GUTTER_COL * 2 + 4;

/** Single sign char for a row kind (space for ctx). */
export function SignChar(props: { kind: "add" | "del" | "ctx" }) {
  const ch = () => (props.kind === "add" ? "+" : props.kind === "del" ? "-" : " ");
  const fg = () => (props.kind === "ctx" ? palette.fgDim : diffSignFg(props.kind));
  return (
    <text fg={fg()} flexShrink={0} wrapMode="none">
      {`${ch()} `}
    </text>
  );
}

interface Truncated {
  spans: ShikiSpan[];
  cut: boolean;
}

/** Span-aware truncation: slice spans so text + the `…` marker fit the
 * visible budget (the marker used to land one column PAST it). */
function cutSpans(spans: ShikiSpan[], budget: number): Truncated {
  const total = spans.reduce((n, span) => n + span.text.length, 0);
  if (total <= budget) return { spans, cut: false };
  return { spans: sliceSpans(spans, Math.max(0, budget - 1)), cut: true };
}

/** First `budget` characters of a span run. */
function sliceSpans(spans: ShikiSpan[], budget: number): ShikiSpan[] {
  const out: ShikiSpan[] = [];
  let used = 0;
  for (const span of spans) {
    if (used >= budget) break;
    const take = budget - used;
    out.push(span.text.length <= take ? span : { text: span.text.slice(0, take), fg: span.fg });
    used += Math.min(span.text.length, take);
  }
  return out;
}

/**
 * One content row of shiki spans, truncated at budget with a dim `…`
 * marker. Falls back to one plain span when shiki is unavailable.
 */
export function TokenSpans(props: {
  spans: ShikiSpan[] | null;
  fallbackText: string;
  fallbackFg: string;
  budget: number;
  /** fg override for token colors (e.g. keep text readable on tinted bg). */
  tokenFg?: (span: ShikiSpan) => string | undefined;
}) {
  const cut = createMemo(() => {
    const source = props.spans ?? [{ text: props.fallbackText, fg: props.fallbackFg }];
    return cutSpans(source, Math.max(0, props.budget));
  });
  return (
    <>
      <For each={cut().spans}>
        {(span) => (
          <text fg={props.tokenFg ? props.tokenFg(span) : span.fg} flexShrink={0} wrapMode="none">
            {span.text}
          </text>
        )}
      </For>
      <Show when={cut().cut}>
        <text fg={palette.fgDim} flexShrink={0}>
          …
        </text>
      </Show>
    </>
  );
}
