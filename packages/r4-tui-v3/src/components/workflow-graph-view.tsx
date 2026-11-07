/**
 * Workflow Graph View — Phase 9
 *
 * Displays the autonomous workflow as a DAG.
 * Nodes: Agent, Knowledge, Experiment, Decision, Review, Publication
 * Highlights: current node, completed nodes, waiting nodes, blocked nodes, future nodes
 * Clicking any node shows details.
 *
 * UI/UX Principles Applied:
 * - Color + symbol for node status (accessibility: not color-only)
 * - Clear visual hierarchy with section headers
 * - Selection feedback with background highlight
 * - Consistent spacing between nodes
 */

import { For, Show, createMemo } from "solid-js";
import { agentRuntime, agentActions } from "../stores/agent-runtime";
import { actions, workspace } from "../stores";
import { palette, statusColor, statusIcon } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real panel width (34% of the detail pane) — was fixed 36. */
const graphCap = () => panePanelWidth(0.34);
import { t, bold, dim } from "@opentui/core";
import { StyledTextView } from "../ui/StyledTextView";
import type { WorkflowNode, WorkflowNodeType, WorkflowNodeStatus } from "../types";
import { workflowStore } from "../stores/workflow";
import { WORKFLOW_EMPTY } from "../lib/empty-hints";
import { Panel } from "../ui/Panel";

/** CURRENT only when a node is actively running — not the first pending step. */
export function resolveCurrentWorkflowNodeId(nodes: WorkflowNode[]): string | null {
  return nodes.find((n) => n.status === "running")?.id ?? null;
}

// ── Node Type Styling ──────────────────────────────────────────────────

const nodeTypeColors: Record<WorkflowNodeType, string> = {
  agent: palette.accent,
  knowledge: palette.knowledge,
  experiment: palette.tasks,
  decision: palette.accent2,
  review: palette.knowledge,
  publication: palette.dashboard,
};

const nodeTypeIcons: Record<WorkflowNodeType, string> = {
  agent: "●",
  knowledge: "✦",
  experiment: "E",
  decision: "?",
  review: "R",
  publication: "▣",
};

const nodeStatusColors: Record<WorkflowNodeStatus, string> = {
  pending: palette.fgDim,
  running: palette.statusInfo,
  completed: palette.statusOk,
  failed: palette.statusErr,
  blocked: palette.statusWarn,
  waiting: palette.statusWarn,
};

const nodeStatusIcons: Record<WorkflowNodeStatus, string> = {
  pending: "○",
  running: "◐",
  completed: "✓",
  failed: "✖",
  blocked: "⚠",
  waiting: "⏳",
};

// ── Node View ──────────────────────────────────────────────────────────

/**
 * Map a WorkflowNode label to a WorkflowStep errors array.
 * Uses label matching as the bridge between the graph store and workflow store.
 */
function getNodeErrors(label: string): string[] {
  const step = workflowStore.steps.find((s) => s.label === label);
  return step?.errors ?? [];
}

function WorkflowNodeView(props: { node: WorkflowNode; isCurrent: boolean; isSelected: boolean }) {
  const statusColor = () => nodeStatusColors[props.node.status] ?? palette.fgDim;
  const typeIcon = () => nodeTypeIcons[props.node.type] ?? "?";
  const statusIcon = () => nodeStatusIcons[props.node.status] ?? "○";
  const nodeErrors = createMemo(() => getNodeErrors(props.node.label));
  const highlight = () => props.isSelected || props.isCurrent;
  const labelText = () => {
    const max = highlight() ? 22 : 30;
    const label = props.node.label;
    return label.length > max ? `${label.slice(0, max - 1)}…` : label;
  };

  return (
    <box
      flexDirection="column"
      gap={0}
      height={highlight() ? 2 : 1}
      overflow="hidden"
      backgroundColor={props.isSelected ? palette.bgActive : props.isCurrent ? palette.bgActive : undefined}
      onMouseDown={() => {
        agentActions.selectWorkflowNode(props.node.id);
        actions.selectObject(`wf:${props.node.id}`);
      }}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={props.isSelected ? palette.accent : props.isCurrent ? palette.accent : statusColor()}>
          {truncate(
            `${statusIcon()} ${typeIcon()} ${labelText()}${props.isSelected ? " ←" : props.isCurrent ? " ●" : ""}`,
            36,
          )}
        </text>
      </box>
      <Show when={nodeErrors().length > 0}>
        <For each={nodeErrors()}>
          {(error) => (
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.statusErr}>{truncate(`  └─ ${error}`, graphCap())}</text>
            </box>
          )}
        </For>
      </Show>
    </box>
  );
}

// ── Main Component ──────────────────────────────────────────────────────

export function WorkflowGraphView() {
  const nodes = createMemo(() => agentRuntime.workflowGraph.nodes);
  const edges = createMemo(() => agentRuntime.workflowGraph.edges);
  const currentNodeId = createMemo(() => resolveCurrentWorkflowNodeId(nodes()));

  // Group nodes by status for summary
  const statusSummary = createMemo(() => {
    const summary: Record<string, number> = {};
    for (const node of nodes()) {
      summary[node.status] = (summary[node.status] ?? 0) + 1;
    }
    return summary;
  });

  return (
    <Panel
      width="34%"
      height="100%"
      flexShrink={0}
      alignSelf="stretch"
      accent={palette.infrastructure}
      title="Workflow Graph"
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      {/* Status Summary — only when graph has nodes */}
      <Show when={nodes().length > 0}>
        <box height={1} width="100%" overflow="hidden" marginBottom={1}>
          <text fg={palette.fgDim}>
            {`✓ ${statusSummary().completed ?? 0}  ● ${statusSummary().running ?? 0}  ○ ${statusSummary().pending ?? 0}  ✖ ${statusSummary().failed ?? 0}`}
          </text>
        </box>
      </Show>

      {/* Workflow Nodes (DAG) */}
      <Show
        when={nodes().length > 0}
        fallback={
          <text fg={palette.fgDim} children={t`${dim(WORKFLOW_EMPTY)}`} />
        }
      >
        <For each={nodes()}>
          {(node) => (
            <WorkflowNodeView
              node={node}
              isCurrent={node.id === currentNodeId()}
              isSelected={workspace.selectedObjectId === `wf:${node.id}`}
            />
          )}
        </For>
      </Show>

      {/* Edge count (debugging) */}
      <Show when={edges().length > 0}>
        <StyledTextView fg={palette.fgDim} marginTop={1} text={t`${dim(`${edges().length} edges in workflow`)}`} />
      </Show>
    </Panel>
  );
}
