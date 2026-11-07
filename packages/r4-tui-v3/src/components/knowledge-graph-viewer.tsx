/**
 * Knowledge Graph Viewer — project goals/tasks/knowledge as a navigable list.
 * Selection is solely workspace.selectedObjectId (feeds Context). No local highlight lie.
 */

import { For, Show, createMemo, createSignal, createEffect } from "solid-js";
import { workspace, actions } from "../stores";
import { agentRuntime } from "../stores/agent-runtime";
import { KeyTrap } from "./key-trap";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import type { KnowledgeNode, KnowledgeEdge } from "../types";
import type { KeyEvent } from "@opentui/core";

import { letterShortcutsBlocked } from "../lib/space-shortcuts";
import { Panel } from "../ui/Panel";

function researchGraphPaneFocused(): boolean {
  if (workspace.paneView !== "research") return false;
  if (workspace.verifyInputFocused) return false;
  // Overlays own focus — do not fight Start actions / slash / help KeyTraps.
  if (letterShortcutsBlocked()) return false;
  // researchFocus is the Tab-cycle source of truth — do not let a leftover
  // bottom tab (explain/strategy) block graph after a full Tab lap.
  return (workspace.researchFocus ?? "graph") === "graph";
}

export const nodeTypeColors: Record<string, string> = {
  hypothesis: palette.accent2,
  experiment: palette.tasks,
  evidence: palette.knowledge,
  finding: palette.statusOk,
  paper: palette.knowledge,
  dataset: palette.infrastructure,
  code: palette.statusInfo,
  notebook: palette.accent,
  figure: palette.statusWarn,
  goal: palette.dashboard,
  task: palette.tasks,
  knowledge: palette.knowledge,
};
export const nodeTypeIcons: Record<string, string> = {
  hypothesis: "H",
  experiment: "E",
  evidence: "◆",
  finding: "F",
  paper: "P",
  dataset: "D",
  code: "C",
  notebook: "N",
  figure: "▲",
  goal: "G",
  task: "T",
  knowledge: "K",
};
const LABEL_MAX = 56;

/** Full labels — truncate only at display so ellipsis is visible. */
export function formatGraphRowLabel(
  node: Pick<KnowledgeNode, "label" | "type" | "confidence">,
  icon: string,
  max = LABEL_MAX,
): string {
  const conf =
    node.confidence !== undefined && node.confidence > 0
      ? ` ${Math.round(node.confidence * 100)}%`
      : "";
  return truncate(`${icon} ${node.label}${conf}`, max);
}

/** Exported for tests — builds the same node list the UI navigates. */
export function buildProjectGraph(): { nodes: KnowledgeNode[]; edges: KnowledgeEdge[] } {
  const nodes: KnowledgeNode[] = [];
  const edges: KnowledgeEdge[] = [];
  for (const goal of workspace.goals) {
    nodes.push({
      id: goal.id,
      label: goal.statement,
      type: "goal",
      status: goal.status,
    });
  }
  for (const task of workspace.tasks) {
    const isExperiment = /^experiment\b/i.test(task.title);
    nodes.push({
      id: task.id,
      label: task.title,
      type: isExperiment ? "experiment" : "task",
      status: task.status,
    });
    if (task.goalId) edges.push({ from: task.goalId, to: task.id, label: "decomposes" });
  }
  for (const k of workspace.knowledge) {
    nodes.push({
      id: k.id,
      label: k.name,
      type: "knowledge",
      status: k.status,
    });
  }
  for (const hyp of agentRuntime.missionControl.hypotheses) {
    nodes.push({
      id: hyp.id,
      label: hyp.statement,
      type: "hypothesis",
      status: "proposed",
      confidence: hyp.confidence,
    });
  }
  return { nodes, edges };
}

function Line(props: { fg?: string; children: string }) {
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

/** Keyboard index derived from selectedObjectId when possible. */
export function graphCursorIndex(nodes: KnowledgeNode[], selectedId: string | null): number {
  if (!selectedId || nodes.length === 0) return 0;
  const idx = nodes.findIndex((n) => n.id === selectedId);
  return idx >= 0 ? idx : 0;
}

export function KnowledgeGraphViewer(props: {
  /**
   * When false (Knowledge space embed): display + mouse only — no KeyTrap.
   * A second KeyTrap on Knowledge stole Tab and showed a stray cursor.
   */
  interactive?: boolean;
  /**
   * When false, catalog ↑↓ on F4 does not move this list's highlight.
   * Click a graph row to select it for Context.
   */
  highlightFromWorkspace?: boolean;
  /**
   * Node types to omit (e.g. ["knowledge"] on F4 so the graph shows the
   * T/E/hypothesis story and the right list owns the catalog files).
   */
  excludeTypes?: Array<KnowledgeNode["type"]>;
}) {
  const interactive = () => props.interactive !== false;
  const followWorkspace = () => props.highlightFromWorkspace !== false;
  const graph = createMemo(() => {
    const g = buildProjectGraph();
    const excluded = props.excludeTypes;
    if (!excluded || excluded.length === 0) return g;
    const drop = new Set(excluded);
    const nodes = g.nodes.filter((n) => !drop.has(n.type));
    const keep = new Set(nodes.map((n) => n.id));
    const edges = g.edges.filter((e) => keep.has(e.from) && keep.has(e.to));
    return { nodes, edges };
  });
  const selectedId = createMemo(() => workspace.selectedObjectId);
  const [cursor, setCursor] = createSignal(0);
  const [pinnedId, setPinnedId] = createSignal<string | null>(null);

  createEffect(() => {
    if (!followWorkspace()) return;
    const nodes = graph().nodes;
    const id = selectedId();
    if (nodes.length === 0) {
      setCursor(0);
      return;
    }
    if (!id) {
      setCursor(0);
      return;
    }
    // Off-graph selection (agent:/wf:) — do not fake-highlight a row.
    const idx = nodes.findIndex((n) => n.id === id);
    if (idx >= 0) setCursor(idx);
  });

  const handleKey = (event: KeyEvent) => {
    if (!interactive()) return;
    const nodes = graph().nodes;
    if (nodes.length === 0) return;
    const base = graphCursorIndex(nodes, selectedId());
    if (event.name === "up" || event.name === "k") {
      // k is Knowledge (global); only treat as vim-up when Shift? No — k is Knowledge.
      // Use only arrow up for navigation (j still down for vim users without k).
      if (event.name === "k") return;
      event.preventDefault();
      const next = Math.max(0, base - 1);
      setCursor(next);
      actions.selectObject(nodes[next]!.id);
    } else if (event.name === "down" || event.name === "j") {
      event.preventDefault();
      const next = Math.min(nodes.length - 1, base + 1);
      setCursor(next);
      actions.selectObject(nodes[next]!.id);
    }
    // Enter / k: handled by global research.graph.* keymap
  };

  const hint = () => {
    if (!interactive()) return null;
    if (workspace.paneView !== "research") return null;
    if (!researchGraphPaneFocused()) return null;
    return "↑↓ · Enter · k";
  };

  return (
    <Panel
      flexGrow={1}
      flexShrink={1}
      minHeight={12}
      height="100%"
      alignSelf="stretch"
      accent={
        interactive() && researchGraphPaneFocused() ? palette.borderFocus : palette.knowledge
      }
      title="Project graph"
      tone={interactive() && researchGraphPaneFocused() ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <Show when={hint()}>
        <Line fg={palette.fgDim}>{hint()!}</Line>
      </Show>
      <scrollbox flexGrow={1} minHeight={0} stickyScroll stickyStart="top" scrollY overflow="hidden" width="100%">
        <For each={graph().nodes}>
          {(node, i) => {
            const isSelected = () =>
              followWorkspace() ? selectedId() === node.id : pinnedId() === node.id;
            const color = () => nodeTypeColors[node.type] ?? palette.fg;
            const icon = () => nodeTypeIcons[node.type] ?? "?";
            return (
              <box
                height={1}
                width="100%"
                overflow="hidden"
                backgroundColor={isSelected() ? palette.bgActive : undefined}
                onMouseDown={() => {
                  setCursor(i());
                  setPinnedId(node.id);
                  actions.selectObject(node.id);
                }}
              >
                <text
                  fg={isSelected() ? palette.accent : color()}
                  onMouseDown={() => {
                    setCursor(i());
                    setPinnedId(node.id);
                    actions.selectObject(node.id);
                  }}
                >
                  {formatGraphRowLabel(node, icon())}
                </text>
              </box>
            );
          }}
        </For>
        <Show when={graph().nodes.length === 0}>
          <Line fg={palette.fgDim}>Graph empty — /project import → init → plan</Line>
        </Show>
      </scrollbox>
      <Show when={interactive()}>
        <KeyTrap paneScoped focused={researchGraphPaneFocused()} onKeyDown={handleKey} />
      </Show>
    </Panel>
  );
}
