/**
 * Knowledge constellation — Sigma.js (WebGL canvas) + force-atlas2 layout.
 *
 * Wave B swap (2026-08-23): replaces the hand-rolled 33ms physics tick with
 * the sigma/graphology toolchain (the same class Obsidian-style local-graph
 * plugins use). Canvas labels, camera pan/zoom, hover neighborhood dimming,
 * and hit-testing now come from a maintained OSS renderer instead of ~200
 * bespoke lines.
 *
 * Notes / simplifications vs. the old mini-sim:
 * - Layout is computed synchronously (FA2: ≤120 iterations ≈ few ms at our
 *   scale) rather than an infinite animation loop — the map settles, CPU idles.
 * - Position continuity between refreshes is preserved via a pos-cache map
 *   (old x/y wins, new nodes join on the seed ring), then a lighter re-layout.
 * - Spawn "pulse" is conveyed as size emphasis instead of an animated ring
 *   (sigma labels/hover carry the salience; no per-frame timer needed).
 * - Oversized payloads still get a hub-first slice (MAX_SIM_NODES) so the
 *   layout never degrades into a hairball.
 *
 * WebGL fallback: if the GPU context is unavailable the component renders a
 * hint instead of crashing the whole view (offline/remote webview cases).
 */

import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import Sigma from 'sigma';
import type {
  GraphContentPayload,
  KnowledgeEdge,
  KnowledgeNode,
} from '@/types/protocol';
import { satelliteColor, semanticKind, typeColor } from './knowledge-visual';

const CANVAS_HEIGHT = 340;
/* Hub-first slice for oversized payloads: FA2 quality degrades into a
   hairball well before the renderer does; the full list stays in panels. */
const MAX_SIM_NODES = 400;
const DIM_RGBA = 'rgba(120, 132, 150, 0.08)';
const NODE_LABEL_MAX = 32;
interface SatInfo {
  graphId: string;
  name: string;
  color: string;
}

function nodeLabelText(node: KnowledgeNode): string {
  const text = String(node.name ?? node.label ?? node.id ?? '?');
  return text.length > NODE_LABEL_MAX ? `${text.slice(0, NODE_LABEL_MAX - 1)}…` : text;
}

function nodeRadius(degree: number): number {
  return 2 + Math.min(9, Math.sqrt(Math.max(degree, 0)) * 1.6);
}

interface BuildResult {
  graph: Graph;
  renderedCount: number;
}

/* Edge style contract: class-distrinct colors at visible size on the dark
   card (user report: "same color as background"). */
const EDGE_MAIN = 'rgba(150, 165, 180, 0.55)';
const EDGE_SAT = 0.75;

function edgeColor(satelliteColor: string | undefined): string {
  return satelliteColor ?? EDGE_MAIN;
}

/** Payload → graphology graph (FA2 as entryway layout, ring-seeded). */
function buildConstellationGraph(
  nodes: KnowledgeNode[],
  edges: KnowledgeEdge[],
  satellites: GraphContentPayload[],
  posCache: ReadonlyMap<string, { x: number; y: number }>,
  pulsing: ReadonlySet<string>,
  viewMode: 'overview' | 'detail' = 'overview',
  detachedIds: ReadonlySet<string> = new Set<string>(),
): BuildResult {
  const degreeById = new Map<string, number>();
  const safeEdges = (Array.isArray(edges) ? edges : []).filter(Boolean);
  // Overview hides chunk+file shells: documents + entities only (chunks
  // are noise per user report — they're already covered by the doc detail).
  const safeNodes = (Array.isArray(nodes) ? nodes : []).filter(Boolean).filter((node) => {
    if (detachedIds.has(String(node.id))) return false;
    if (viewMode === 'overview') {
      const meta = (node.metadata ?? {}) as Record<string, unknown>;
      const kind = String(meta.kind ?? '');
      return kind !== 'chunk' && kind !== 'file';
    }
    return true;
  });

  interface EdgeLite {
    source: string;
    target: string;
    color: string;
  }
  const liteEdges: EdgeLite[] = [];
  for (const edge of safeEdges) {
    const from = String(edge.from);
    const to = String(edge.to);
    degreeById.set(from, (degreeById.get(from) ?? 0) + 1);
    degreeById.set(to, (degreeById.get(to) ?? 0) + 1);
    liteEdges.push({ source: from, target: to, color: EDGE_MAIN });
  }

  // Hub-first slice for oversized payloads.
  const displayNodes =
    safeNodes.length > MAX_SIM_NODES
      ? [...safeNodes]
          .sort((a, b) => (degreeById.get(String(b.id)) ?? 0) - (degreeById.get(String(a.id)) ?? 0))
          .slice(0, MAX_SIM_NODES)
      : safeNodes;

  interface SimRow {
    id: string;
    degree: number;
    color: string;
    label: string;
    satellite?: SatInfo;
  }
  const satRows: SimRow[] = [];
  satellites.filter(Boolean).forEach((content, satIndex) => {
    const color = satelliteColor(satIndex);
    const sat: SatInfo = {
      graphId: content.graph_id,
      name: content.name ?? content.graph_id,
      color,
    };
    (content.nodes ?? []).filter(Boolean).slice(0, 24).forEach((node) => {
      satRows.push({
        id: String(node.id),
        degree: degreeById.get(String(node.id)) ?? 0,
        color,
        label: nodeLabelText(node),
        satellite: sat,
      });
    });
    (content.edges ?? []).filter(Boolean).forEach((edge) =>
      liteEdges.push({
        source: String(edge.from),
        target: String(edge.to),
        color: edgeColor(color),
      }),
    );
  });

  const graph = new Graph<Record<string, unknown>, Record<string, unknown>>({
    multi: false,
    type: 'directed',
  });

  const displayNodeIds = new Set(displayNodes.map((node) => String(node.id)));
  const visibleEdges = liteEdges.filter(
    (edge) => displayNodeIds.has(edge.source) && displayNodeIds.has(edge.target),
  );

  const ring = 200;
  const totalRows = displayNodes.length + satRows.length;
  displayNodes
    .map(
      (node): SimRow => ({
        id: String(node.id),
        degree: degreeById.get(String(node.id)) ?? 0,
        color: typeColor(semanticKind(node)),
        label: nodeLabelText(node),
      }),
    )
    .concat(satRows)
    .forEach((row, index) => {
      const angle = (2 * Math.PI * index) / Math.max(totalRows, 1) - Math.PI / 2;
      const cached = posCache.get(row.id);
      const size = nodeRadius(row.degree) * (pulsing.has(row.id) ? 1.4 : 1);
      graph.addNode(row.id, {
        x: cached?.x ?? ring * Math.cos(angle),
        y: cached?.y ?? ring * Math.sin(angle),
        size,
        color: row.color,
        label: row.label,
        labelColor: '#d7dee8',
      });
    });
  for (const edge of visibleEdges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    const key = `${edge.source}->${edge.target}`;
    if (!graph.hasEdge(key)) {
      // No `type:` — sigma's stock line program is the guaranteed renderer;
      // custom program names silently skip when unregistered.
      graph.addEdgeWithKey(key, edge.source, edge.target, {
        color: edge.color,
        size: 1.4,
      });
    }
  }

  // FA2 settling: heavier pass at fresh mounts, gentle when positions cached.
  forceAtlas2.assign(graph, {
    iterations: posCache.size > 0 ? 45 : 120,
    settings: {
      gravity: 1.2,
      scalingRatio: 6,
      barnesHutOptimize: graph.order > 60,
      slowDown: 8,
      edgeWeightInfluence: 0.2,
    },
  });
  return { graph, renderedCount: displayNodes.length };
}

export default function KnowledgeConstellationSigma(props: {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  satellites: GraphContentPayload[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Ids of main-graph nodes that just spawned — size emphasis. */
  pulsing?: ReadonlySet<string>;
}): ReactElement {
  const { nodes, edges, satellites, selectedId, onSelect, pulsing } = props;
  const [viewMode, setViewMode] = useState<'overview' | 'detail'>('overview');
  const [detachedIds, setDetachedIds] = useState<Set<string>>(new Set());
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const posCacheRef = useRef(new Map<string, { x: number; y: number }>());
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [sigmaFailed, setSigmaFailed] = useState(false);
  const [renderedCount, setRenderedCount] = useState(0);

  /* Rebuild the graph whenever the payload (or pulse set) changes. */
  useEffect(() => {
    const previous = graphRef.current;
    previous?.forEachNode((id, attrs) => {
      posCacheRef.current.set(id, { x: attrs.x as number, y: attrs.y as number });
    });
    const { graph, renderedCount: count } = buildConstellationGraph(
      nodes,
      edges,
      satellites,
      posCacheRef.current,
      pulsing ?? new Set<string>(),
      viewMode,
      detachedIds,
    );
    graphRef.current = graph;
    setRenderedCount(count);
    const sigma = sigmaRef.current;
    if (sigma) {
      try {
        sigma.setGraph(graph);
        sigma.refresh();
      } catch (error) {
        console.warn('knowledge graph re-layout failed:', error);
      }
    }
  }, [nodes, edges, satellites, pulsing, viewMode, detachedIds]);

  /* Sigma lifecycle: construct once, bind reducers/events, kill on unmount. */
  useEffect(() => {
    const container = containerRef.current;
    const graph = graphRef.current;
    if (!container || !graph) return;
    let sigma: Sigma;
    try {
      sigma = new Sigma(graph, container, {
        labelSize: 12,
        labelColor: { color: '#d7dee8' },
        labelWeight: 'normal',
        labelRenderedSizeThreshold: 2.5,
        hideEdgesOnMove: true,
        enableEdgeEvents: false,
        minCameraRatio: 0.05,
        maxCameraRatio: 8,
        allowInvalidContainer: false,
      });
    } catch (error) {
      console.warn('sigma init failed (WebGL unavailable?):', error);
      setSigmaFailed(true);
      return;
    }
    sigmaRef.current = sigma;

    sigma.on('clickNode', ({ node }) => {
      // Right-click = detach the node + its edges from the map (manipulation),
      // left-click = select/detour as before.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const domEvent = (window.event as MouseEvent | undefined) ?? (undefined as any);
      if ((domEvent as MouseEvent | undefined)?.button === 2) {
        setDetachedIds((prev) => {
          const next = new Set(prev);
          if (next.has(node)) next.delete(node);
          else next.add(node);
          return next;
        });
        return;
      }
      onSelect(node === selectedId ? null : node);
    });
    sigma.on('enterNode', ({ node }) => setHoverId(node));
    sigma.on('leaveNode', () => setHoverId(null));
    sigma.on('clickStage', () => onSelect(null));

    const observer = new ResizeObserver(() => {
      sigma.resize();
      sigma.refresh();
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      sigma.kill();
      sigmaRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Hover/selection neighborhood dimming via reducers. */
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !sigmaFailed) {
      const focus = hoverId ?? selectedId;
      const neighborhood = new Set<string>();
      if (focus && graph?.hasNode(focus)) {
        neighborhood.add(focus);
        graph.forEachNeighbor(focus, (neighbor) => neighborhood.add(neighbor));
      }
      sigma?.setSetting('nodeReducer', (_node: string, data: Record<string, unknown>) => {
        const view = { ...data };
        const isFocus = focus === (_node as string);
        if (isFocus) {
          view.size = (data.size as number) * 1.25;
          view.highlighted = true;
          return view;
        }
        if (focus && !neighborhood.has(_node as string)) {
          view.color = DIM_RGBA;
          view.label = '';
          view.size = (data.size as number) * 0.6;
        }
        return view;
      });
      sigma?.setSetting('edgeReducer', (_edge: string, data: Record<string, unknown>) => {
        if (!focus) return data;
        const graphNow = graphRef.current;
        if (!graphNow) return data;
        const ends = graphNow.extremities(_edge);
        return neighborhood.has(ends[0]) && neighborhood.has(ends[1])
          ? data
          : { ...data, color: 'rgba(120,132,150,0.04)' };
      });
      sigma?.refresh();
    } else {
      /* nothing: renderer failed to come up; hint shows instead */
      return;
    }
  }, [hoverId, selectedId, sigmaFailed]);

  const hint = useMemo(() => {
    const total = Array.isArray(nodes) ? nodes.filter(Boolean).length : 0;
    return total > MAX_SIM_NODES ? `showing ${renderedCount} of ${total} (hub-first)` : null;
  }, [nodes, renderedCount]);

  if (sigmaFailed) {
    return (
      <div className="r4-force-hint" aria-live="polite">
        Graph rendering unavailable in this environment (WebGL disabled).
        Graph data still reachable via the list + inspector.
      </div>
    );
  }

  return (
    <>
      <div className="r4-force-toolbar" style={{ display: 'flex', gap: 8, margin: '6px 0 2px' }}>
        <button
          type="button"
          className={`r4-btn${viewMode === 'overview' ? ' r4-btn-active' : ''}`}
          title="Documents + entities only (chunks hidden)"
          onClick={() => setViewMode('overview')}
        >
          overview
        </button>
        <button
          type="button"
          className={`r4-btn${viewMode === 'detail' ? ' r4-btn-active' : ''}`}
          title="All kinds — including chunk/file shells"
          onClick={() => setViewMode('detail')}
        >
          detail
        </button>
        {detachedIds.size > 0 && (
          <button
            type="button"
            className="r4-btn"
            title="Restore hidden nodes"
            onClick={() => setDetachedIds(new Set())}
          >
            × restore {detachedIds.size}
          </button>
        )}
      </div>
      <div
        ref={containerRef}
        className="r4-force-canvas r4-sigma-container"
        role="img"
        aria-label="Knowledge graph"
        style={{ width: '100%', height: CANVAS_HEIGHT }}
        onContextMenu={(e) => e.preventDefault()}
      />
      <div className="r4-force-hint">
        drag to pan · scroll to zoom · click a node for connections · right-click = hide node
        {hint && ` · ${hint}`}
      </div>
    </>
  );
}
