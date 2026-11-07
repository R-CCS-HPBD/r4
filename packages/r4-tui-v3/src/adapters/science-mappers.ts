/**
 * Map UMRS server-spine payloads (snake_case, ADR-031 wire shapes) to the
 * typed science rows. Pure functions — the only place field extraction lives,
 * and the only place absence is translated to explicit ``null``/empty rows.
 * Nothing here fabricates: missing vector rows stay missing, unknown states
 * stay the raw server string.
 */

import type {
  CatalogAsset,
  CatalogLinks,
  CatalogReadView,
  CatalogSearchResult,
  CatalogStats,
  ConsistencyState,
  GraphContent,
  GraphContentEdge,
  GraphContentNode,
  GraphProposal,
  GraphSummary,
  LineageNodeRow,
  LineageReport,
  ReconcileAsset,
  ReconcileReport,
} from "../types/science";

type Raw = Record<string, unknown>;

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

// ── Catalogue plane ─────────────────────────────────────────────────────

export function mapCatalogLinks(raw: unknown): CatalogLinks | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Raw;
  const documentId = str(r["document_id"], "");
  if (!documentId) return null;
  return {
    documentId,
    linkMethod: str(r["link_method"], ""),
    chunkNodes: num(r["chunk_nodes"], 0),
    vectorRows: arr(r["vector_rows"]).map((v) => {
      const vr = (v ?? {}) as Raw;
      return { model: strOrNull(vr["model"]), dimension: numOrNull(vr["dimension"]) };
    }),
  };
}

export function mapCatalogAsset(raw: unknown): CatalogAsset {
  const r = (raw ?? {}) as Raw;
  return {
    s3Key: str(r["s3_key"], ""),
    state: str(r["state"], ""),
    summary: str(r["summary"], ""),
    deterministicSummary: str(r["deterministic_summary"], ""),
    modality: str(r["detected_modality"], ""),
    tags: arr(r["tags"]).map((t) => String(t)),
    relevanceScore: num(r["relevance_score"], 0),
    links: mapCatalogLinks(r["links"]),
  };
}

export function mapCatalogSearch(payload: Raw): CatalogSearchResult {
  return {
    query: str(payload["query"], ""),
    projectId: str(payload["project_id"], ""),
    results: arr(payload["results"]).map(mapCatalogAsset),
  };
}

export function mapCatalogRead(payload: Raw): CatalogReadView {
  return {
    s3Key: str(payload["s3_key"], ""),
    offset: num(payload["offset"], 0),
    length: num(payload["length"], 0),
    totalSize: num(payload["total_size"], 0),
    truncated: payload["truncated"] === true,
    text: str(payload["text"], ""),
    readSource: str(payload["read_source"], "raw"),
    links: mapCatalogLinks(payload["links"]),
  };
}

export function mapCatalogStats(payload: Raw): CatalogStats {
  const toIntMap = (v: unknown): Record<string, number> => {
    const out: Record<string, number> = {};
    if (v && typeof v === "object") {
      for (const [k, val] of Object.entries(v as Raw)) {
        const n = numOrNull(val);
        if (n !== null) out[k] = n;
      }
    }
    return out;
  };
  return {
    projectId: str(payload["project_id"], ""),
    objectCount: num(payload["object_count"], 0),
    states: toIntMap(payload["states"]),
    modalities: toIntMap(payload["modalities"]),
    canonicalDocuments: numOrNull(payload["canonical_documents"]),
    chunkNodes: numOrNull(payload["chunk_nodes"]),
    vectorRows: numOrNull(payload["vector_rows"]),
  };
}

// ── Graph registry (0069) ───────────────────────────────────────────────

export function mapGraphSummary(raw: unknown): GraphSummary {
  const r = (raw ?? {}) as Raw;
  return {
    graphId: str(r["graph_id"], ""),
    name: str(r["name"], ""),
    description: str(r["description"], ""),
    ownerId: str(r["owner_id"], ""),
    projectId: strOrNull(r["project_id"]),
    kind: str(r["kind"], ""),
    status: str(r["status"], ""),
    createdAt: str(r["created_at"], ""),
  };
}

export function mapGraphList(payload: Raw): GraphSummary[] {
  return arr(payload["graphs"]).map(mapGraphSummary);
}

export function mapGraphContent(payload: Raw): GraphContent {
  const nodes: GraphContentNode[] = arr(payload["nodes"]).map((n) => {
    const r = (n ?? {}) as Raw;
    return {
      id: str(r["id"], str(r["node_id"], "")),
      label: str(r["label"], str(r["name"], "")),
      type: str(r["type"], str(r["node_type"], "knowledge")),
      projectId: str(r["project_id"], ""),
      graphId: str(r["graph_id"], ""),
    };
  });
  const edges: GraphContentEdge[] = arr(payload["edges"]).map((e) => {
    const r = (e ?? {}) as Raw;
    return {
      from: str(r["from"], ""),
      to: str(r["to"], ""),
      type: str(r["type"], str(r["label"], "related")),
    };
  });
  return {
    graphId: str(payload["graph_id"], ""),
    name: str(payload["name"], ""),
    kind: str(payload["kind"], ""),
    projectId: strOrNull(payload["project_id"]),
    nodes,
    edges,
    truncated: payload["truncated"] === true,
  };
}

export function mapGraphProposal(raw: unknown): GraphProposal {
  const r = (raw ?? {}) as Raw;
  const actorRaw = r["actor"];
  const actor = typeof actorRaw === "string" ? actorRaw : str((actorRaw as Raw | undefined)?.["kind"], "");
  return {
    proposalId: str(r["proposal_id"], str(r["id"], "")),
    graphId: str(r["graph_id"], ""),
    kind: str(r["kind"], ""),
    status: str(r["status"], ""),
    createdAt: str(r["created_at"], ""),
    actor,
    summary: str(r["summary"], str(r["reason"], "")),
  };
}

// ── Plane reconciler (ADR-031 Invariant 4, server-derived) ──────────────

const CONSISTENCY_STATES: readonly ConsistencyState[] = [
  "COMPLETE",
  "EMBEDDING_PENDING",
  "PARTIAL",
  "ORPHANED",
];

function asConsistencyState(v: unknown): ConsistencyState {
  const s = str(v, "");
  return (CONSISTENCY_STATES as readonly string[]).includes(s) ? (s as ConsistencyState) : "PARTIAL";
}

export function mapReconcileAsset(raw: unknown): ReconcileAsset {
  const r = (raw ?? {}) as Raw;
  const reasons = arr(r["reasons"]).map((x) => String(x));
  return {
    documentId: str(r["document_id"], ""),
    state: asConsistencyState(r["state"]),
    reasons,
    chunkNodes: num(r["chunk_nodes"], 0),
    hasCatalogue: r["has_catalogue"] === true,
    hasEmbeddings: r["has_embeddings"] === true,
  };
}

export function mapReconcileReport(payload: Raw): ReconcileReport {
  const countsRaw = (payload["counts"] ?? {}) as Raw;
  const counts = {} as Record<ConsistencyState, number>;
  for (const state of CONSISTENCY_STATES) {
    counts[state] = num(countsRaw[state], 0);
  }
  return {
    projectId: str(payload["project_id"], ""),
    catalogueAvailable: payload["catalogue_available"] !== false,
    counts,
    assetsSampled: num(payload["assets_sampled"], 0),
    assets: arr(payload["assets"]).map(mapReconcileAsset),
    orphanedVectorRows: num(payload["orphaned_vector_rows"], 0),
    orphanedChunkNodes: num(payload["orphaned_chunk_nodes"], 0),
  };
}

// ── Lineage / provenance (workstream §7) ────────────────────────────────

export function mapLineageNode(raw: unknown): LineageNodeRow {
  const r = (raw ?? {}) as Raw;
  return {
    artifactId: str(r["artifact_id"], ""),
    artifactType: str(r["artifact_type"], ""),
    title: str(r["title"], ""),
    edgeKind: str(r["edge_kind"], ""),
    createdAt: typeof r["created_at"] === "string" ? (r["created_at"] as string) : "",
  };
}

export function mapLineageReport(payload: Raw): LineageReport {
  return {
    artifactId: str(payload["artifact_id"], ""),
    ancestors: arr(payload["ancestors"]).map(mapLineageNode),
    descendants: arr(payload["descendants"]).map(mapLineageNode),
  };
}
