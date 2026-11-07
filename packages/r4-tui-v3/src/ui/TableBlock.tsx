/**
 * TableBlock — render a GFM pipe table as aligned bordered grid.
 *
 * OpenTUI's markdown engine passes tables through as raw text in finalized
 * transcripts; per-line rendering would swallow the alignment, so transcript
 * runs pipe tables through this structured view instead (chars stay monospace,
 * columns keep meaning).
 */
import { For } from "solid-js";
import { palette } from "../themes";
import { fitCell, tableColumnWidths, type TableBlock as TableModel } from "../lib/markdown-tables";

/** `width`: the column the grid must fit (MarkdownBlock's wrapWidth);
 * absent keeps the legacy per-cell cap only. */
export function TableBlock(props: { table: TableModel; width?: number }) {
  const widths = () => tableColumnWidths(props.table, 28, props.width);

  function row(cells: string[]): string {
    return `│ ${cells
      .map((cell, index) => fitCell(cell, widths()[index] ?? 1))
      .join(" │ ")} │`;
  }

  function border(left: string, join: string, right: string): string {
    return widths()
      .map((w) => "─".repeat(w + 2))
      .join(join)
      .replace(/^/, left)
      .replace(/$/, right);
  }

  return (
    <box flexDirection="column" width="100%" overflow="hidden">
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>{border("┌", "┬", "┐")}</text>
      </box>
      <box height={1} overflow="hidden">
        <text fg={palette.fgBright}>{row(props.table.header)}</text>
      </box>
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>{border("├", "┼", "┤")}</text>
      </box>
      <For each={props.table.rows}>
        {(r) => (
          <box height={1} overflow="hidden">
            <text>{row(r)}</text>
          </box>
        )}
      </For>
      <box height={1} overflow="hidden">
        <text fg={palette.fgDim}>{border("└", "┴", "┘")}</text>
      </box>
    </box>
  );
}
