/**
 * Side-by-side diff renderer (open-code parity): old | new panes with
 * status colors — red on deletions/modified-left, green on additions/
 * modified-right. Line numbers gutter. Unified fallback stays in DiffBlock.
 */

import { For, createMemo } from "solid-js";
import { palette } from "../themes";
import { parseUnifiedToRows, capRows, type DiffRow, type ColumnCell } from "../lib/diff-view";

function cellFg(cell: ColumnCell): string {
  if (cell.kind === "del") return palette.diffDel;
  if (cell.kind === "add") return palette.diffAdd;
  if (cell.kind === "mod") return palette.statusWarn;
  return palette.fgDim;
}

function renderCell(cell: ColumnCell, width: number): string {
  const no = cell.no === null ? " ".repeat(4) : String(cell.no).padStart(4, " ");
  const text = cell.text.slice(0, Math.max(0, width - 7));
  return `${no} │ ${text}`;
}

/** One side-by-side row: left pane | right pane, fixed widths. */
function Row(props: { row: DiffRow; colWidth: number }) {
  const leftText = () => renderCell(props.row.left, props.colWidth);
  const rightText = () => renderCell(props.row.right, props.colWidth);
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden">
      <text fg={cellFg(props.row.left)}>{leftText()}</text>
      <text fg={palette.fgDim}>┆</text>
      <text fg={cellFg(props.row.right)}>{rightText()}</text>
    </box>
  );
}

export function SideBySideDiff(props: { diffText: string; width: number; maxRows?: number }) {
  const rows = createMemo(() => {
    const parsed = parseUnifiedToRows(props.diffText);
    return props.maxRows ? capRows(parsed, props.maxRows) : parsed;
  });
  const colWidth = () => Math.max(12, Math.floor((props.width - 3) / 2));
  return (
    <box flexDirection="column" width="100%">
      <box height={1} flexDirection="row">
        <text fg={palette.fgDim}>{renderCell({ no: null, text: "OLD".padEnd(Math.max(0, colWidth() - 7)), kind: "ctx" }, colWidth())}</text>
        <text fg={palette.fgDim}>┆</text>
        <text fg={palette.fgDim}>{renderCell({ no: null, text: "NEW".padEnd(Math.max(0, colWidth() - 7)), kind: "ctx" }, colWidth())}</text>
      </box>
      <For each={rows()}>{(row) => <Row row={row} colWidth={colWidth()} />}</For>
    </box>
  );
}
