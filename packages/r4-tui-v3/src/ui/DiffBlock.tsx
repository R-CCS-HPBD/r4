/**
 * Unified diff block — opencode <diff> parity over shiki colors:
 * tinted add/del/hunk rows, old/new line-number gutter, colored sign
 * column, token spans inside each row (tokenized per file side, one call
 * per side), span-aware `…` truncation at the caller's column width.
 */

import { For, Show, createMemo } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import { palette } from "../themes";
import { diffHeader } from "../lib/diff-highlight";
import { langForPath } from "../lib/code-highlight";
import {
  newSideLines,
  numberedDiffLines,
  oldSideLines,
  sideTokenIndex,
  truncateVis,
  type NumberedDiffLine,
} from "../lib/diff-numbered";
import { highlightWithShiki, normalizeShikiLang, shikiReady, type ShikiSpan } from "../lib/code-shiki";
import { diffRowBg, Gutter, GUTTER_WIDTH, SignChar, TokenSpans } from "./diff-chrome";

/** fg per row kind (live palette reads — never frozen at module eval). */
function kindFg(kind: string): string {
  if (kind === "add") return palette.diffAdd;
  if (kind === "del") return palette.diffDel;
  if (kind === "hunk") return palette.diffHunk;
  if (kind === "meta") return palette.accent2;
  return palette.fg;
}

/** First `+++ b/path` in the diff (lang guess when the caller has none). */
function langFromDiff(content: string): string | null {
  for (const line of content.split("\n")) {
    if (line.startsWith("+++")) {
      const file = line.replace(/^\+\+\+\s+/, "").replace(/^[ab]\//, "").trim();
      const lang = normalizeShikiLang(langForPath(file));
      if (lang) return lang;
    }
  }
  return null;
}

/** One-shot shiki spans for both file sides (null → plain fallback). */
function useSideSpans(lines: () => NumberedDiffLine[], lang: () => string | null) {
  return createMemo(() => {
    const l = lang();
    if (!l || !shikiReady()) return null;
    const oldSpans = highlightWithShiki(oldSideLines(lines()), l);
    const newSpans = highlightWithShiki(newSideLines(lines()), l);
    return oldSpans && newSpans ? { old: oldSpans, new: newSpans } : null;
  });
}

function HunkMetaRow(props: { line: NumberedDiffLine; width: number }) {
  const isHunk = () => props.line.kind === "hunk";
  return (
    <box
      height={1}
      width="100%"
      flexDirection="row"
      overflow="hidden"
      backgroundColor={isHunk() ? diffRowBg("hunk") : undefined}
    >
      <Gutter leftNo={null} rightNo={null} />
      <text fg={kindFg(props.line.kind)}>
        {truncateVis(props.line.text, Math.max(1, props.width - GUTTER_WIDTH))}
      </text>
    </box>
  );
}

function BodyRow(props: {
  line: NumberedDiffLine;
  spans: ShikiSpan[] | null;
  width: number;
}) {
  const kind = () => props.line.kind as "add" | "del" | "ctx";
  const bg = () => {
    const k = kind();
    return k === "ctx" ? undefined : diffRowBg(k as "add" | "del");
  };
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden" backgroundColor={bg()}>
      <Gutter leftNo={props.line.oldNo} rightNo={props.line.newNo} />
      <SignChar kind={kind()} />
      <TokenSpans
        spans={props.spans}
        fallbackText={props.line.text}
        fallbackFg={kindFg(kind())}
        budget={Math.max(1, props.width - GUTTER_WIDTH - 2)}
      />
    </box>
  );
}

export function DiffBlock(props: { content: string; width?: number; filePath?: string }) {
  const dims = useTerminalDims();
  const width = () => props.width ?? dims().width;
  const lines = createMemo(() => numberedDiffLines(props.content));
  const indexes = createMemo(() => sideTokenIndex(lines()));
  const header = () => diffHeader(props.content);
  const lang = createMemo(() =>
    normalizeShikiLang(props.filePath ? langForPath(props.filePath) : null) ?? langFromDiff(props.content),
  );
  const sideSpans = useSideSpans(lines, lang);
  const spansFor = (i: number): ShikiSpan[] | null => {
    const sides = sideSpans();
    const ref = indexes()[i];
    if (!sides || !ref) return null;
    return ref.side === "old" ? sides.old[ref.index] ?? null : sides.new[ref.index] ?? null;
  };
  return (
    <box flexDirection="column" overflow="hidden">
      <Show when={header()}>
        {(h) => (
          <box height={1} overflow="hidden">
            <text fg={palette.accent2}>
              {`+${h().added} −${h().removed}${h().files.length > 0 ? `  ${h().files.slice(0, 3).join(" ")}${h().files.length > 3 ? " …" : ""}` : ""}`}
            </text>
          </box>
        )}
      </Show>
      <For each={lines()}>
        {(line, i) =>
          line.kind === "hunk" || line.kind === "meta" ? (
            <HunkMetaRow line={line} width={width()} />
          ) : (
            <BodyRow line={line} spans={spansFor(i())} width={width()} />
          )
        }
      </For>
    </box>
  );
}
