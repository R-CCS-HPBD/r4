/**
 * Project knowledge graph fetch + layout for the Graph view.
 *
 * Mirrors the UMRS ``/gw/knowledge/graph route payload (nodes carry an
 * ``id alias of node_id; edges carry from/to/label). Deterministic radial
 * seed layout — physics in the view animates away from it.
 */

import { gatewayFetch } from "./gateway";

/* ── wire shapes (mirror umrs.api.knowledge /graph response) ─────────────── */

export interface KnowledgeNodePayload {
  id?: string;
  node_id?: string;
  node_type?: string;
  type?: string;
  title?: string;
  name?: string;
  label?: string;
  confidence?: number;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeEdgePayload {
  from?: string;
  to?: string;
  label?: string;
  source_id?: string;
  target_id?: string;
  relation_type?: string;
}

export interface KnowledgeGraphPayload {
  project_id?: string;
  phase?: string;
  nodes?: KnowledgeNodePayload[];
  edges?: KnowledgeEdgePayload[];
}

/** Fetch one project's knowledge graph; throws on HTTP error. */
export async function fetchProjectKnowledgeGraph(
  projectId: string,
): Promise<KnowledgeGraphPayload> {
  const resp = await gatewayFetch(
    `/gw/knowledge/graph?project_id=${encodeURIComponent(projectId)}`,
  );
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    const detail = text.slice(0, 120) || `HTTP ${resp.status}`;
    throw new Error(detail);
  }
  return (await resp.json()) as KnowledgeGraphPayload;
}

/* ── Real node kind → color (r4-tui-v3/webview parity vocabulary) ────────── */

export const TYPE_COLORS: Record<string, string> = {
  goal: "#9b51e0",
  hypothesis: "#b088f9",
  task: "#f2c94c",
  experiment: "#f2c94c",
  evidence: "#6fcf97",
  finding: "#56ccf2",
  result: "#fbbf24",
  paper: "#6fcf97",
  dataset: "#f2994a",
  code: "#34d399",
  notebook: "#bb6bd9",
  figure: "#eb5757",
  material: "#f472b6",
  property: "#a78bfa",
  method: "#34d399",
  model: "#38bdf8",
  process: "#fb923c",
  candidate: "#38bdf8",
  knowledge: "#56ccf2",
};

const TYPE_RADIUS: Record<string, number> = {
  goal: 18,
  hypothesis: 16,
  task: 14,
  experiment: 14,
  finding: 13,
  result: 12,
  paper: 12,
  dataset: 12,
  material: 16,
};

export function normalizeNodeType(node: KnowledgeNodePayload): string {
  return String(node.node_type ?? node.type ?? "knowledge").toLowerCase();
}

export function typeColor(nodeType: string | undefined): string {
  return TYPE_COLORS[(nodeType ?? "knowledge").toLowerCase()] ?? "#94a3b8";
}

export function typeRadius(nodeType: string | undefined): number {
  return TYPE_RADIUS[(nodeType ?? "knowledge").toLowerCase()] ?? 11;
}

function firstLabel(node: KnowledgeNodePayload): unknown {
  return node.name ?? node.title ?? node.label ?? node.id ?? node.node_id;
}

export function nodeLabel(node: KnowledgeNodePayload): string {
  const text = String(firstLabel(node) ?? "?");
  return text.length > 22 ? `${text.slice(0, 20)}…` : text;
}

export function nodeId(node: KnowledgeNodePayload): string {
  return String(node.id ?? node.node_id ?? "");
}

export function edgeEndpoints(edge: KnowledgeEdgePayload): { from: string; to: string } {
  return {
    from: String(edge.from ?? edge.source_id ?? ""),
    to: String(edge.to ?? edge.target_id ?? ""),
  };
}

export function edgeLabel(edge: KnowledgeEdgePayload): string {
  return String(edge.label ?? edge.relation_type ?? "related");
}

/* ── View-model mapping ──────────────────────────────────────────────────── */

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  /** Timestamp (ms) when the UI first saw this node; 0 = existed before the view opened. */
  bornAt: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
}

/** Deterministic ring seed — sorted by type then id for stable refreshes. */
export function layoutKnowledgeNodes(
  nodes: KnowledgeNodePayload[],
  width: number,
  height: number,
): GraphNode[] {
  const sorted = [...nodes]
    .map((n) => ({ id: nodeId(n), type: normalizeNodeType(n), node: n }))
    .filter((n) => n.id)
    .sort((a, b) => (a.type + a.id).localeCompare(b.type + b.id));

  const cx = width / 2;
  const cy = height / 2;
  const ring = Math.max(80, Math.min(width, height) / 2 - 70);

  return sorted.map(({ id, type, node }, index) => {
    const angle = (2 * Math.PI * index) / Math.max(sorted.length, 1) - Math.PI / 2;
    // Single node sits at the center; otherwise evenly on the ring.
    const single = sorted.length === 1;
    return {
      id,
      label: nodeLabel(node),
      kind: type,
      x: single ? cx : cx + ring * Math.cos(angle),
      y: single ? cy : cy + ring * Math.sin(angle),
      // Small deterministic kick so physics settles organically per node.
      vx: ((index % 5) - 2) * 0.12,
      vy: ((index % 3) - 1) * 0.14,
      radius: typeRadius(type),
      bornAt: 0,
    };
  });
}

/**
 * Merge a fresh seeded layout into the currently displayed nodes.
 * Existing nodes keep their live physics position and bornAt; nodes present
 * when the view opened (prev empty) stay bornAt = 0; genuinely new arrivals
 * get a staggered bornAt timestamp that drives the spawn pulse animation.
 */
export function mergeGraphNodes(
  prev: GraphNode[],
  seeded: GraphNode[],
  now: number,
): { merged: GraphNode[]; added: GraphNode[] } {
  const prevById = new Map(prev.map((n) => [n.id, n]));
  const added: GraphNode[] = [];
  const merged = seeded.map((seed) => {
    const live = prevById.get(seed.id);
    if (live) {
      return { ...seed, x: live.x, y: live.y, vx: live.vx, vy: live.vy, bornAt: live.bornAt };
    }
    const node = { ...seed, bornAt: prev.length === 0 ? 0 : now + added.length * 120 };
    added.push(node);
    return node;
  });
  return { merged, added };
}

export function mapKnowledgeEdges(edges: KnowledgeEdgePayload[]): GraphEdge[] {
  return edges
    .map((edge) => {
      const { from, to } = edgeEndpoints(edge);
      return { source: from, target: to, label: edgeLabel(edge) };
    })
    .filter((edge) => edge.source && edge.target);
}

/** Types actually present (legend shows only live rows, no dead swatches). */
export function presentTypes(nodes: GraphNode[]): string[] {
  return [...new Set(nodes.map((n) => n.kind))].sort();
}
