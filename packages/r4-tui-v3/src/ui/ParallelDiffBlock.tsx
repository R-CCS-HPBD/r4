/**
 * Side-by-side diff renderer — opencode <diff view="split"> parity over
 * shiki: per-cell line number (4-col gutter), sign column, tinted cell bg
 * (add/del), token spans inside each cell (old/new sides tokenized once),
 * span-aware `…` truncation. Column width comes from the CALLER (chat
 * column or overlay width) — never raw terminal width, which sprawled
 * whitespace into a 200-col terminal (maintainer screenshot 2026-09-30).
 */

import { For, Show, createMemo } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import { palette } from "../themes";
import { diffHeader } from "../lib/diff-highlight";
import { toSideBySide, type DiffSide, type SideBySideRow } from "../lib/parallel-diff";
import { numberSideBySideRows, truncateVis } from "../lib/diff-numbered";
import { langForPath } from "../lib/code-highlight";
import { highlightWithShiki, normalizeShikiLang, shikiReady, type ShikiSpan } from "../lib/code-shiki";
import { diffRowBg, numCell, TokenSpans } from "./diff-chrome";

type NumberedRow = SideBySideRow & { leftNo: number | null; rightNo: number | null };

const CELL_GUTTER = 5; // 4-wide number + space
const CELL_SIGN = 2; // sign + space

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

/** One cell: gutter(own number) + sign + tokens, tinted by side kind. */
function Cell(props: {
  side: DiffSide;
  no: number | null;
  spans: ShikiSpan[] | null;
  budget: number;
  sign: "+" | "-" | " ";
}) {
  const bg = () =>
    props.side.kind === "add"
      ? diffRowBg("add")
      : props.side.kind === "del"
        ? diffRowBg("del")
        : undefined;
  const signFg = () =>
    props.sign === "+"
      ? palette.diffAdd
      : props.sign === "-"
        ? palette.diffDel
        : palette.fgDim;
  return (
    <box height={1} width={props.budget} flexDirection="row" overflow="hidden" backgroundColor={bg()}>
      <text fg={palette.fgDim}>{`${numCell(props.no)} `}</text>
      <text fg={signFg()}>{`${props.side.kind === "empty" ? " " : props.sign} `}</text>
      <Show when={props.side.kind !== "empty"}>
        <TokenSpans
          spans={props.spans}
          fallbackText={props.side.text}
          fallbackFg={palette.fg}
          budget={Math.max(1, props.budget - CELL_GUTTER - CELL_SIGN)}
        />
      </Show>
    </box>
  );
}

export function ParallelDiffBlock(props: { content: string; width?: number; filePath?: string }) {
  const dims = useTerminalDims();
  const width = () => props.width ?? dims().width;
  const rows = createMemo<NumberedRow[]>(() => numberSideBySideRows(toSideBySide(props.content)));
  const header = () => diffHeader(props.content);
  const cellWidth = () => Math.max(20, Math.floor((width() - 1) / 2));
  const lang = createMemo(() =>
    normalizeShikiLang(props.filePath ? langForPath(props.filePath) : null) ?? langFromDiff(props.content),
  );
  /** Left sides in order = old-file lines; right sides = new-file lines. */
  const sideSpans = createMemo(() => {
    const l = lang();
    if (!l || !shikiReady()) return null;
    const lefts = rows()
      .map((r) => r.left)
      .filter((s) => s.kind === "del" || s.kind === "ctx")
      .map((s) => s.text);
    const rights = rows()
      .map((r) => r.right)
      .filter((s) => s.kind === "add" || s.kind === "ctx")
      .map((s) => s.text);
    const oldSpans = highlightWithShiki(lefts, l);
    const newSpans = highlightWithShiki(rights, l);
    return oldSpans && newSpans ? { old: oldSpans, new: newSpans } : null;
  });
  const spansFor = (row: NumberedRow, side: "left" | "right"): ShikiSpan[] | null => {
    const sides = sideSpans();
    if (!sides) return null;
    const seq = side === "left" ? row.left : row.right;
    if (seq.kind === "empty") return null;
    const pool = side === "left" ? sides.old : sides.new;
    let index = 0;
    for (const r of rows()) {
      const s = side === "left" ? r.left : r.right;
      if (r === row) return pool[index] ?? null;
      if (s.kind !== "empty") index++;
    }
    return null;
  };
  return (
    <box flexDirection="column" overflow="hidden" width="100%">
      <Show when={header()}>
        {(h) => (
          <box height={1} overflow="hidden">
            <text fg={palette.accent2}>
              {`+${h().added} −${h().removed}${h().files.length > 0 ? `  ${h().files.slice(0, 3).join(" ")}${h().files.length > 3 ? " …" : ""}` : ""}`}
            </text>
          </box>
        )}
      </Show>
      <For each={rows()}>
        {(row) =>
          row.kind === "full" ? (
            <box
              height={1}
              width="100%"
              flexDirection="row"
              overflow="hidden"
              backgroundColor={row.text?.startsWith("@@") ? diffRowBg("hunk") : undefined}
            >
              <text fg={row.text?.startsWith("@@") ? palette.diffHunk : palette.accent2}>
                {truncateVis(row.text ?? "", Math.max(1, width() - 1))}
              </text>
            </box>
          ) : (
            <box height={1} width="100%" flexDirection="row" overflow="hidden">
              <Cell
                side={row.left}
                no={row.leftNo}
                spans={spansFor(row, "left")}
                budget={cellWidth()}
                sign="-"
              />
              <Cell
                side={row.right}
                no={row.rightNo}
                spans={spansFor(row, "right")}
                budget={cellWidth()}
                sign="+"
              />
            </box>
          )
        }
      </For>
    </box>
  );
}

export { preferSideBySide } from "../lib/parallel-diff";
