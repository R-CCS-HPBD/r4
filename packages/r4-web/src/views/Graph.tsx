/**
 * Graph view — knowledge graph visualization.
 *
 * Mirrors r4-tui-v3 KnowledgeGraphViewer component. Provides an interactive
 * node-based visualization of the knowledge graph with pan/zoom, node info,
 * and relationship edges rendered in canvas.
 */

import { useRef, useEffect, useState, useMemo, useCallback } from "react";
import { Shell } from "../components/Shell";
import { DEFAULT_PROJECT_ID } from "../companion/project";
import {
  fetchProjectKnowledgeGraph,
  layoutKnowledgeNodes,
  mapKnowledgeEdges,
  mergeGraphNodes,
  presentTypes,
  typeColor,
  type GraphEdge,
  type GraphNode,
} from "../companion/knowledge";

/** Canvas size the physics + layout constants target. */
const CANVAS_W = 800;
const CANVAS_H = 500;

/** Live growth: poll cadence and spawn-pulse duration. */
const POLL_MS = 4000;
const PULSE_MS = 4500;

/** Project to render: `?project=<id>` query param wins, else the console default. */
function initialProjectId(): string {
  const fromQuery = new URLSearchParams(window.location.search).get("project");
  return fromQuery && fromQuery.trim() ? fromQuery.trim() : DEFAULT_PROJECT_ID;
}

/** Fetch lifecycle for the knowledge graph. */
type LoadState =
  | { status: "loading" }
  | { status: "ready" }
  | { status: "empty" }
  | { status: "error"; message: string };
/* ── Main view ─────────────────────────────────────────────────────────── */

export function Graph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loadState, setLoadState] = useState<LoadState>({ status: "loading" });
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [panX, setPanX] = useState(0);
  const [panY, setPanY] = useState(0);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [recentAdds, setRecentAdds] = useState<GraphNode[]>([]);
  /* Viewed project: chat-created projects are visible via ?project=<id> or
     the top-bar field (interim until the console project switcher lands). */
  const [projectId, setProjectId] = useState<string>(initialProjectId);
  const [projectInput, setProjectInput] = useState<string>(initialProjectId);
  /* Poll reads latest nodes without re-arming effects; startedRef marks the
     initial attempt so silent polls never mask a first-load error. */
  const nodesRef = useRef<GraphNode[]>([]);
  const startedRef = useRef(false);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  /* Project switch: full reset (declared before the polling effect so it
     runs first on the same commit) — no cross-project pulse artifacts. */
  useEffect(() => {
    nodesRef.current = [];
    startedRef.current = false;
    setNodes([]);
    setEdges([]);
    setSelectedNode(null);
    setRecentAdds([]);
  }, [projectId]);

  /* Live fetch — initial load honors empty/error; silent polls merge so
     existing nodes keep physics positions and newcomers spawn with bornAt
     stamps driving the pulse ring, activity ticker, and '+N new' badge. */
  const load = useCallback(async () => {
    const first = !startedRef.current;
    startedRef.current = true;
    if (first) {
      setLoadState({ status: "loading" });
      setSelectedNode(null);
    }
    try {
      const payload = await fetchProjectKnowledgeGraph(projectId);
      const seeded = layoutKnowledgeNodes(payload.nodes ?? [], CANVAS_W, CANVAS_H);
      const graphEdges = mapKnowledgeEdges(payload.edges ?? []);
      const { merged, added } = mergeGraphNodes(nodesRef.current, seeded, Date.now());
      setNodes(merged);
      setEdges(graphEdges);
      if (!first && added.length > 0) {
        setRecentAdds((prev) => [...added, ...prev].slice(0, 4));
      }
      setLoadState(seeded.length === 0 ? { status: "empty" } : { status: "ready" });
    } catch (error) {
      /* Silent poll failures keep the last good graph on screen. */
      if (first) {
        setLoadState({
          status: "error",
          message: error instanceof Error ? error.message : "fetch failed",
        });
      }
    }
  }, [projectId]);

  /* Initial fetch + live polling (paused while the tab is hidden). */
  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  /* Open a different project id — also syncs ?project= for shareable links. */
  const openProject = useCallback(() => {
    const next = projectInput.trim();
    if (!next || next === projectId) return;
    const url = new URL(window.location.href);
    url.searchParams.set("project", next);
    window.history.replaceState(null, "", url.toString());
    setProjectId(next);
  }, [projectInput, projectId]);

  /* Physics simulation */
  useEffect(() => {
    const interval = setInterval(() => setNodes(physicsStep), 30);
    return () => clearInterval(interval);
  }, []);

/* ── Physics + draw helpers (module level keeps component CCN low) ─────────── */

function physicsStep(nodes: GraphNode[]): GraphNode[] {
  const next = nodes.map((n) => ({ ...n, x: n.x + n.vx, y: n.y + n.vy }));
  for (let i = 0; i < next.length; i++) {
    for (let j = i + 1; j < next.length; j++) {
      const dx = next[j].x - next[i].x;
      const dy = next[j].y - next[i].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      if (dist >= 200) continue;
      const force = (200 - dist) * 0.005;
      next[i].vx -= (dx / dist) * force;
      next[i].vy -= (dy / dist) * force;
      next[j].vx += (dx / dist) * force;
      next[j].vy += (dy / dist) * force;
    }
  }
  return next.map((n) => ({ ...n, vx: n.vx * 0.95, vy: n.vy * 0.95 }));
}

interface DrawState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  hoveredId: string | null;
  selectedNode: GraphNode | null;
  panX: number;
  panY: number;
  zoom: number;
  now: number;
}

function drawEdge(ctx: CanvasRenderingContext2D, edge: GraphEdge, state: DrawState): void {
  const src = state.nodes.find((n) => n.id === edge.source);
  const tgt = state.nodes.find((n) => n.id === edge.target);
  if (!src || !tgt) return;
  const hits = [edge.source, edge.target];
  const isHighlighted =
    (state.hoveredId && hits.includes(state.hoveredId)) ||
    (state.selectedNode && hits.includes(state.selectedNode.id));
  ctx.beginPath();
  ctx.moveTo(src.x, src.y);
  ctx.lineTo(tgt.x, tgt.y);
  ctx.strokeStyle = isHighlighted ? "rgba(56,189,248,0.8)" : "rgba(51,65,85,0.6)";
  ctx.lineWidth = isHighlighted ? 2 : 1;
  ctx.stroke();
}

function isNodeDimmed(state: DrawState, node: GraphNode): boolean {
  if (!state.hoveredId || state.hoveredId === node.id) return false;
  return !state.edges.some(
    (e) =>
      [e.source, e.target].includes(state.hoveredId as string) &&
      [e.source, e.target].includes(node.id),
  );
}

function drawNode(ctx: CanvasRenderingContext2D, node: GraphNode, state: DrawState): void {
  const isHovered = state.hoveredId === node.id;
  const isSelected = state.selectedNode?.id === node.id;
  const isDimmed = isNodeDimmed(state, node);
  const color = typeColor(node.kind);

  ctx.beginPath();
  ctx.arc(node.x, node.y, node.radius * (isHovered || isSelected ? 1.2 : 1), 0, Math.PI * 2);
  ctx.fillStyle = isDimmed ? `${color}30` : color;
  ctx.fill();
  ctx.strokeStyle = isSelected ? "#ffffff" : `${color}80`;
  ctx.lineWidth = isSelected ? 2.5 : 1;
  ctx.stroke();

  ctx.font = `${isHovered ? "bold " : ""}${isDimmed ? 9 : 11}px -apple-system, sans-serif`;
  ctx.fillStyle = isDimmed ? `${color}40` : "#e2e8f0";
  ctx.textAlign = "center" as CanvasTextAlign;
  ctx.fillText(node.label, node.x, node.y + node.radius + 14);

  /* Spawn pulse — expanding ring while the node is younger than PULSE_MS. */
  if (node.bornAt > 0) {
    const progress = (state.now - node.bornAt) / PULSE_MS;
    if (progress >= 0 && progress < 1) {
      ctx.save();
      ctx.globalAlpha = (1 - progress) * 0.75;
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.radius + 4 + progress * 18, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
    }
  }
}

function drawGraph(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, state: DrawState): void {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();
  ctx.translate(state.panX, state.panY);
  ctx.scale(state.zoom, state.zoom);
  for (const edge of state.edges) drawEdge(ctx, edge, state);
  for (const node of state.nodes) drawNode(ctx, node, state);
  ctx.restore();
}

  /* Render canvas */
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    drawGraph(ctx, canvas, { nodes, edges, hoveredId, selectedNode, panX, panY, zoom, now: Date.now() });
  }, [nodes, edges, zoom, panX, panY, hoveredId, selectedNode]);

  /* Mouse handlers */
  const handleWheel = (e: React.WheelEvent) => {
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom((z) => Math.max(0.3, Math.min(3, z * delta)));
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - panX) / zoom;
    const my = (e.clientY - rect.top - panY) / zoom;

    const clicked = nodes.find((n) => {
      const dx = mx - n.x;
      const dy = my - n.y;
      return dx * dx + dy * dy < (n.radius + 5) * (n.radius + 5);
    });

    if (clicked) {
      setSelectedNode(clicked);
      setHoveredId(clicked.id);
    } else {
      setSelectedNode(null);
      setHoveredId(null);
    }
  };

  /* Connected nodes for selected node info */
  const connectedNodes = useMemo(() => {
    if (!selectedNode) return [];
    return edges
      .filter((e) => e.source === selectedNode.id || e.target === selectedNode.id)
      .map((e) => {
        const otherId = e.source === selectedNode.id ? e.target : e.source;
        return nodes.find((n) => n.id === otherId);
      })
      .filter(Boolean) as GraphNode[];
  }, [selectedNode, edges, nodes]);

  /* Nodes that arrived while this view was open (bornAt set by live polls). */
  const newCount = nodes.reduce((count, n) => count + (n.bornAt > 0 ? 1 : 0), 0);

  return (
    <Shell title="Graph">
      {/* Top bar */}
      <div style={styles.topBar}>
        <span style={styles.nodeCount}>
          {loadState.status === "loading" && "Loading project knowledge…"}
          {loadState.status === "error" && (
            <span style={{ color: "#f87171" }}>Knowledge unavailable: {loadState.message}</span>
          )}
          {(loadState.status === "ready" || loadState.status === "empty") && (
            <>
              {nodes.length} nodes · {edges.length} edges · project {projectId}
              {newCount > 0 && <span style={styles.newBadge}>+{newCount} new</span>}
              <span style={styles.liveWrap} title={`Refreshing every ${POLL_MS / 1000}s`}>
                <span style={styles.liveDot} />
                live
              </span>
            </>
          )}
        </span>
        <input
          style={styles.projectInput}
          value={projectInput}
          onChange={(e) => setProjectInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") openProject();
          }}
          placeholder="project id"
          title="Knowledge project to render (chat project ids work here)"
        />
        <button style={{ ...styles.zoomBtn, width: "auto", padding: "0 0.5rem" }} onClick={openProject}>
          open
        </button>
        <div style={styles.zoomControls}>
          <button style={styles.zoomBtn} onClick={() => setZoom((z) => Math.max(0.3, z * 0.8))}>
            −
          </button>
          <span style={styles.zoomLabel}>{Math.round(zoom * 100)}%</span>
          <button style={styles.zoomBtn} onClick={() => setZoom((z) => Math.min(3, z * 1.25))}>
            +
          </button>
          <button style={styles.zoomBtn} onClick={() => { setZoom(1); setPanX(0); setPanY(0); }}>
            ⟲
          </button>
        </div>
      </div>

      {/* Activity ticker — newest arrivals first, keeps the last few spawns visible. */}
      {recentAdds.length > 0 && (
        <div style={styles.ticker}>
          {recentAdds.slice(0, 3).map((n) => (
            <span key={n.id} style={styles.tickerItem}>
              <span style={{ ...styles.legendDot, backgroundColor: typeColor(n.kind) }} />
              ＋ {n.label}
            </span>
          ))}
        </div>
      )}

      <div style={styles.graphLayout}>
        {loadState.status === "ready" ? (
          <canvas
            ref={canvasRef}
            width={CANVAS_W}
            height={CANVAS_H}
            style={styles.canvas}
            onWheel={handleWheel}
            onMouseDown={handleMouseDown}
            onMouseEnter={() => setHoveredId(null)}
          />
        ) : (
          <div style={{ ...styles.canvas, display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: "0.85rem", textAlign: "center", padding: "1rem" }}>
            {loadState.status === "loading" && "Loading…"}
            {loadState.status === "empty" && (
              <>No knowledge in project “{projectId}” yet.<br />Feed files or run a research loop — nodes appear here as the graph builds.</>
            )}
            {loadState.status === "error" && (
              <>
                Knowledge graph unavailable — {loadState.message}<br />
                <button style={{ ...styles.zoomBtn, width: "auto", padding: "0 0.5rem", marginTop: "0.75rem" }} onClick={() => void load()}>
                  retry
                </button>
              </>
            )}
          </div>
        )}

        {/* Node legend — only types actually present get a swatch */}
        <div style={styles.legend}>
          {presentTypes(nodes).map((kind) => (
            <div key={kind} style={styles.legendItem}>
              <span style={{ ...styles.legendDot, backgroundColor: typeColor(kind) }} />
              <span style={styles.legendLabel}>{kind}</span>
            </div>
          ))}
        </div>

        {/* Selected node detail panel */}
        {selectedNode && (
          <div style={styles.detailPanel}>
            <h3 style={styles.detailTitle}>{selectedNode.label}</h3>
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>Type:</span>
              <span style={{ color: typeColor(selectedNode.kind) }}>{selectedNode.kind}</span>
            </div>
            <div style={styles.detailRow}>
              <span style={styles.detailLabel}>ID:</span>
              <span style={{ fontFamily: "monospace" }}>{selectedNode.id}</span>
            </div>
            {connectedNodes.length > 0 && (
              <>
                <div style={styles.detailSectionTitle}>Connections ({connectedNodes.length})</div>
                {connectedNodes.map((cn) => (
                  <div key={cn.id} style={styles.connectionItem}>
                    <span style={{ color: typeColor(cn.kind) }}>{cn.label}</span>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>
    </Shell>
  );
}

/* ── Styles ────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  topBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "0.75rem",
  },
  nodeCount: {
    fontSize: "0.75rem",
    color: "#64748b",
  },
  liveWrap: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.3rem",
    marginLeft: "0.75rem",
    color: "#34d399",
    fontSize: "0.7rem",
    textTransform: "uppercase" as const,
    letterSpacing: "0.08em",
  },
  liveDot: {
    width: "7px",
    height: "7px",
    borderRadius: "50%",
    backgroundColor: "#34d399",
    boxShadow: "0 0 6px #34d399",
  },
  newBadge: {
    marginLeft: "0.75rem",
    padding: "0.1rem 0.45rem",
    borderRadius: "999px",
    backgroundColor: "#064e3b",
    color: "#6ee7b7",
    fontWeight: 600,
  },
  ticker: {
    display: "flex",
    gap: "1rem",
    alignItems: "center",
    marginBottom: "0.5rem",
    fontSize: "0.72rem",
    color: "#6ee7b7",
    overflow: "hidden",
  },
  tickerItem: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.35rem",
    whiteSpace: "nowrap" as const,
  },
  zoomControls: {
    display: "flex",
    alignItems: "center",
    gap: "0.375rem",
  },
  projectInput: {
    width: "140px",
    height: "28px",
    padding: "0 0.5rem",
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "6px",
    color: "#e2e8f0",
    fontSize: "0.75rem",
    marginLeft: "auto",
    marginRight: "0.375rem",
  },
  zoomBtn: {
    width: "28px",
    height: "28px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "6px",
    color: "#94a3b8",
    cursor: "pointer",
    fontSize: "0.875rem",
  },
  zoomLabel: {
    fontSize: "0.75rem",
    color: "#94a3b8",
    minWidth: "3ch",
    textAlign: "center" as const,
  },
  graphLayout: {
    display: "grid",
    gridTemplateColumns: "1fr 200px",
    gap: "1rem",
  },
  canvas: {
    backgroundColor: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "8px",
    cursor: "grab",
  },
  legend: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.375rem",
  },
  legendItem: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
  },
  legendDot: {
    width: "10px",
    height: "10px",
    borderRadius: "50%",
    flexShrink: 0,
  },
  legendLabel: {
    fontSize: "0.75rem",
    color: "#94a3b8",
    textTransform: "capitalize" as const,
  },
  detailPanel: {
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "8px",
    padding: "1rem",
    gridRow: "1 / 3",
  },
  detailTitle: {
    fontSize: "0.875rem",
    fontWeight: 700,
    marginBottom: "0.5rem",
    color: "#f1f5f9",
  },
  detailRow: {
    display: "flex",
    gap: "0.5rem",
    marginBottom: "0.25rem",
    fontSize: "0.8rem",
  },
  detailLabel: {
    color: "#64748b",
    minWidth: "5ch",
  },
  detailSectionTitle: {
    fontSize: "0.7rem",
    color: "#64748b",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginTop: "0.75rem",
    marginBottom: "0.375rem",
    borderTop: "1px solid #334155",
    paddingTop: "0.5rem",
  },
  connectionItem: {
    fontSize: "0.8rem",
    padding: "0.25rem 0",
    paddingLeft: "0.5rem",
    borderLeft: "2px solid #334155",
  },
};
