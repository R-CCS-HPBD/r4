/**
 * GraphCanvas — SVG DAG of watched workflow executions.
 *
 * Layout: executions in a left column, their steps fanning right.
 * Pure inline-SVG (no deps) sized to the node set; click selects a node
 * and the Studio detail panel drives actions (cancel / approve gate).
 */

import { useMemo, useState } from 'react';
import type { ExecutionSnapshot, WorkflowStepSnapshot } from '@/types/protocol';

export interface GraphNode {
  id: string;
  kind: 'execution' | 'step';
  label: string;
  sublabel?: string;
  /** normalized lowercase status (completed|running|failed|cancelled|pending|waiting*) */
  status: string;
  /** pointer to the column-owning execution for step nodes. */
  parentId?: string;
  /** vertical lane index shared by an execution and its steps. */
  row: number;
  /** horizontal order for step nodes inside the lane. */
  order?: number;
  selected?: boolean;
  /** manual-gate step waiting for approval. */
  gate?: boolean;
  payload?: ExecutionSnapshot | WorkflowStepSnapshot;
}

interface Positioned {
  node: GraphNode;
  x: number;
  y: number;
  w: number;
  h: number;
}

const EXEC_W = 150;
const EXEC_H = 56;
const STEP_W = 120;
const STEP_H = 44;
const LANE_H = 108;
const EXEC_X = 90;
const STEP_X0 = 300;
const STEP_DX = 150;
const PAD_TOP = 30;

function layout(nodes: GraphNode[]): { placed: Positioned[]; width: number; height: number } {
  const placed: Positioned[] = [];
  let maxSteps = 0;
  for (const node of nodes) {
    if (node.kind === 'execution') {
      placed.push({
        node,
        x: EXEC_X,
        y: PAD_TOP + node.row * LANE_H + (LANE_H - EXEC_H) / 2,
        w: EXEC_W,
        h: EXEC_H,
      });
    } else {
      const order = node.order ?? 0;
      maxSteps = Math.max(maxSteps, order + 1);
      placed.push({
        node,
        x: STEP_X0 + order * STEP_DX,
        y: PAD_TOP + node.row * LANE_H + (LANE_H - STEP_H) / 2,
        w: STEP_W,
        h: STEP_H,
      });
    }
  }
  const rows = nodes.reduce((max, node) => Math.max(max, node.row), -1) + 1;
  return {
    placed,
    width: Math.max(320, STEP_X0 + Math.max(maxSteps, 1) * STEP_DX + 100),
    height: Math.max(160, rows * LANE_H + PAD_TOP + 20),
  };
}

function edgePath(from: Positioned, to: Positioned): string {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

/** Find the preceding step node inside the same execution lane. */
function findPrevStep(placed: Positioned[], step: Positioned): Positioned | undefined {
  const order = step.node.order ?? 0;
  return placed.find(
    (candidate) =>
      candidate.node.kind === 'step' &&
      candidate.node.parentId === step.node.parentId &&
      candidate.node.order === order - 1,
  );
}

/** Parent-link + sequence edges for one step node. */
function edgesForStep(
  placed: Positioned[],
  byId: Map<string, Positioned>,
  step: Positioned,
): Array<{ key: string; d: string; to: Positioned }> {
  const edges: Array<{ key: string; d: string; to: Positioned }> = [];
  const parent = step.node.parentId ? byId.get(step.node.parentId) : undefined;
  if (parent) {
    edges.push({ key: `${parent.node.id}->${step.node.id}`, d: edgePath(parent, step), to: step });
  }
  const prev = findPrevStep(placed, step);
  if (prev) {
    edges.push({ key: `${prev.node.id}->${step.node.id}`, d: edgePath(prev, step), to: step });
  }
  return edges;
}

/** All SVG edges: execution→first-step plus step→step chains per lane. */
function buildEdges(placed: Positioned[]): Array<{ key: string; d: string; to: Positioned }> {
  const byId = new Map(placed.map((p) => [p.node.id, p]));
  return placed
    .filter((p) => p.node.kind === 'step' && p.node.parentId)
    .flatMap((p) => edgesForStep(placed, byId, p));
}

export default function GraphCanvas(props: {
  nodes: GraphNode[];
  onSelect: (id: string) => void;
}) {
  const { nodes, onSelect } = props;
  const [zoom, setZoom] = useState(1);

  const { placed, width, height } = useMemo(() => layout(nodes), [nodes]);

  const edges = useMemo(() => buildEdges(placed), [placed]);

  return (
    <div className="r4-graph">
      <div className="r4-graph-toolbar">
        <span className="r4-graph-legend">
          <i className="r4-dot r4-dot-planned" /> planned
          <i className="r4-dot r4-dot-running" /> running
          <i className="r4-dot r4-dot-completed" /> done
          <i className="r4-dot r4-dot-waiting" /> gate
          <i className="r4-dot r4-dot-failed" /> failed
        </span>
        <span className="r4-graph-zoom">
          <button type="button" className="r4-btn" onClick={() => setZoom((z) => Math.max(0.4, z - 0.15))} title="Zoom out">−</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button type="button" className="r4-btn" onClick={() => setZoom((z) => Math.min(2.2, z + 0.15))} title="Zoom in">+</button>
        </span>
      </div>
      <div className="r4-graph-scroll">
        <svg
          className="r4-graph-svg"
          width={width * zoom}
          height={height * zoom}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Workflow execution graph"
        >
          <defs>
            <marker
              id="r4-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 8 4 L 0 8 z" className="r4-graph-arrowhead" />
            </marker>
          </defs>
          {edges.map((edge) => (
            <path
              key={edge.key}
              d={edge.d}
              className={
                'r4-graph-edge' +
                (edge.to.node.status === 'running' ? ' r4-graph-edge-active' : '') +
                (['completed', 'done', 'succeeded'].includes(edge.to.node.status)
                  ? ' r4-graph-edge-done'
                  : '')
              }
              markerEnd="url(#r4-arrow)"
            />
          ))}
          {placed.map((p) => {
            const classes = [
              'r4-graph-node',
              `r4-graph-node-${p.node.status.replace(/\s+/g, '-')}`,
              p.node.selected ? 'r4-graph-node-selected' : '',
              p.node.gate ? 'r4-graph-node-gate' : '',
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <g
                key={p.node.id}
                className={classes}
                tabIndex={0}
                role="button"
                aria-pressed={p.node.selected}
                onClick={() => onSelect(p.node.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(p.node.id);
                  }
                }}
              >
                <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={10} />
                {p.node.gate && (
                  <rect
                    x={p.x - 3}
                    y={p.y - 3}
                    width={p.w + 6}
                    height={p.h + 6}
                    rx={12}
                    className="r4-graph-gate-ring"
                  />
                )}
                <text x={p.x + p.w / 2} y={p.y + (p.node.sublabel ? p.h / 2 - 4 : p.h / 2 + 4)} textAnchor="middle" className="r4-graph-label">
                  {p.node.label.length > 18 ? `${p.node.label.slice(0, 17)}…` : p.node.label}
                </text>
                {p.node.sublabel && (
                  <text x={p.x + p.w / 2} y={p.y + p.h / 2 + 13} textAnchor="middle" className="r4-graph-sublabel">
                    {p.node.sublabel}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
