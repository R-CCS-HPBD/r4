/**
 * Server Graph Panel — canonical spine (0069 multi-graph registry) surface.
 *
 * Renders what `/umrs/knowledge/graphs` + `/graphs/{id}/content` returned
 * (science store), lane-labeled as server truth. When the spine read is
 * unavailable the caller renders the plan-projection fallback instead —
 * the lane label is the honesty contract: this panel never shows
 * workspace-derived rows under a "server" title.
 */

import { For, Show, createMemo } from "solid-js";
import { scienceActions, scienceStore, selectedProposal } from "../stores/science";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real panel width (45% lane of the pane) — was fixed 52. */
const sgCap = () => panePanelWidth(0.45, 8);
import { nodeTypeColors, nodeTypeIcons } from "./knowledge-graph-viewer";
import { Panel } from "../ui/Panel";
import type { GraphProposal, GraphSummary } from "../types/science";

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function graphRowLabel(graph: GraphSummary): string {
  const scope = graph.kind === "project" ? (graph.projectId ?? "?") : graph.kind;
  return truncate(`${graph.name}  · ${scope} · ${graph.status}`, sgCap());
}

/** Agent graph-extension proposal inbox row (human authorization gate).
 * The selected row (▸) is the one the a / r keys decide; a click selects. */
function ProposalRow(props: {
  proposal: GraphProposal;
  selected: boolean;
  onSelect: () => void;
  onDecide: (approved: boolean) => void;
}) {
  return (
    <box
      height={1}
      width="100%"
      overflow="hidden"
      backgroundColor={props.selected ? palette.bgActive : undefined}
    >
      <text fg={palette.statusWarn} onMouseDown={() => props.onSelect()}>
        {truncate(`${props.selected ? "▸" : "?"} ${props.proposal.summary || props.proposal.kind}`, sgCap())}
      </text>
      <text fg={palette.fgDim} onMouseDown={() => props.onDecide(true)}>
        {" [a]pprove"}
      </text>
      <text fg={palette.fgDim} onMouseDown={() => props.onDecide(false)}>
        {" [r]eject"}
      </text>
    </box>
  );
}

/* a/r decision keys reach the SELECTED proposal (▸; [ / ] move it, K1.5)
 * through the global keymap's knowledge-space bindings
 * (knowledge.proposal.*, keymap/index.ts decideSelectedProposal, round 2): a
 * trap here could never hold focus beside the pane's list trap. */

/** "2/7 agent proposals": where the selection is, first, so a narrow lane
 * still shows it (review of K1 part A: the header showed a count only, and
 * [ / ] were not discoverable). */
export function proposalHeader(): string {
  const rows = scienceStore.proposals;
  const at = rows.findIndex((row) => row.proposalId === selectedProposal()?.proposalId);
  return truncate(`${Math.max(0, at) + 1}/${rows.length} agent proposals`, sgCap());
}

/** The inbox keys, on their own row (the lane is narrow). */
export function proposalKeysHint(): string {
  const keys = scienceStore.proposals.length > 1 ? "[ ] select · a/r decide" : "a/r decide";
  return truncate(keys, sgCap());
}

/** Rows shown: four, scrolled so the selected proposal stays visible. */
const PROPOSAL_ROWS = 4;
function proposalWindow(): GraphProposal[] {
  const rows = scienceStore.proposals;
  const at = Math.max(0, rows.findIndex((row) => row.proposalId === selectedProposal()?.proposalId));
  const start = Math.max(0, Math.min(at - PROPOSAL_ROWS + 1, rows.length - PROPOSAL_ROWS));
  return rows.slice(start, start + PROPOSAL_ROWS);
}

export function ServerGraphPanel() {
  const content = createMemo(() => scienceStore.graphContent);
  const selectedId = createMemo(() => scienceStore.selectedGraphId);

  const laneLabel = createMemo(() => {
    const c = content();
    if (!c) return "canonical spine";
    const trunc = c.truncated ? " · truncated" : "";
    return `canonical spine · ${c.name} · ${c.nodes.length} nodes / ${c.edges.length} edges${trunc}`;
  });

  return (
    <Panel
      flexDirection="column"
      width="45%"
      accent={palette.knowledge}
      title="Server graph"
      padding={1}
      overflow="hidden"
    >
      <Line fg={palette.fgDim}>{laneLabel()}</Line>
      <Show when={scienceStore.graphs.length > 1}>
        <box height={3} flexDirection="column" overflow="hidden">
          <For each={scienceStore.graphs.slice(0, 3)}>
            {(graph) => {
              const isSelected = () => selectedId() === graph.graphId;
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={isSelected() ? palette.bgActive : undefined}
                  onMouseDown={() => void scienceActions.loadGraphContent(graph.graphId)}
                >
                  <text fg={isSelected() ? palette.fgBright : palette.fgDim}>
                    {truncate(`▸ ${graphRowLabel(graph)}`, 48)}
                  </text>
                </box>
              );
            }}
          </For>
        </box>
      </Show>
      <Show
        when={content()}
        fallback={<Line fg={palette.fgDim}>no server graph loaded — picker loads on mount</Line>}
      >
        <scrollbox flexGrow={1} minHeight={4} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
          <For each={content()!.nodes.slice(0, 40)}>
            {(node) => (
              <Line fg={nodeTypeColors[node.type] ?? palette.fg}>
                {truncate(`${nodeTypeIcons[node.type] ?? "?"} ${node.label}`, sgCap())}
              </Line>
            )}
          </For>
          <Show when={content()!.nodes.length > 40}>
            <Line fg={palette.fgDim}>{`… ${content()!.nodes.length - 40} more nodes (bounded read)`}</Line>
          </Show>
        </scrollbox>
      </Show>
      <Show when={scienceStore.proposals.length > 0}>
        <Line fg={palette.accent2}>{proposalHeader()}</Line>
        <Line fg={palette.fgDim}>{proposalKeysHint()}</Line>
        <For each={proposalWindow()}>
          {(proposal) => (
            <ProposalRow
              proposal={proposal}
              selected={selectedProposal()?.proposalId === proposal.proposalId}
              onSelect={() => scienceActions.selectProposal(proposal.proposalId)}
              onDecide={(approved) => void scienceActions.decideProposal(proposal.proposalId, approved)}
            />
          )}
        </For>
      </Show>
    </Panel>
  );
}
