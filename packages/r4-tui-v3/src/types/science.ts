/**
 * ADR-031 server-spine domain rows for the science catalog / knowledge graph
 * surfaces. These mirror UMRS wire payloads — the TUI never re-derives
 * consistency state; it renders what the server returned (Invariant 4).
 */

/** ADR-031 cross-plane links block (hydrated server-side, fail-open). */
export interface CatalogLinks {
  documentId: string;
  linkMethod: string;
  chunkNodes: number;
  vectorRows: Array<{ model: string | null; dimension: number | null }>;
}

/** One row of the science catalogue (physical-existence plane). */
export interface CatalogAsset {
  s3Key: string;
  state: string;
  summary: string;
  deterministicSummary: string;
  modality: string;
  tags: string[];
  relevanceScore: number;
  /** null = no canonical document minted — honest absence, not an error. */
  links: CatalogLinks | null;
}

export interface CatalogSearchResult {
  query: string;
  projectId: string;
  results: CatalogAsset[];
}

/** Byte-bounded read payload of one catalogue asset (§4/§5 surface). */
export interface CatalogReadView {
  s3Key: string;
  offset: number;
  length: number;
  totalSize: number;
  truncated: boolean;
  text: string;
  readSource: string;
  links: CatalogLinks | null;
}

export interface CatalogStats {
  projectId: string;
  objectCount: number;
  states: Record<string, number>;
  modalities: Record<string, number>;
  /** null = not computable this process (pool absent / pre-ADR server). */
  canonicalDocuments: number | null;
  chunkNodes: number | null;
  vectorRows: number | null;
}

/** One registered knowledge graph (0069 multi-graph registry row). */
export interface GraphSummary {
  graphId: string;
  name: string;
  description: string;
  ownerId: string;
  projectId: string | null;
  kind: string;
  status: string;
  createdAt: string;
}

export interface GraphContentNode {
  id: string;
  label: string;
  type: string;
  projectId: string;
  graphId: string;
}

export interface GraphContentEdge {
  from: string;
  to: string;
  type: string;
}

export interface GraphContent {
  graphId: string;
  name: string;
  kind: string;
  projectId: string | null;
  nodes: GraphContentNode[];
  edges: GraphContentEdge[];
  truncated: boolean;
}

/** ADR-031 Invariant 4 consistency states — server-derived vocabulary only. */
export type ConsistencyState = "COMPLETE" | "EMBEDDING_PENDING" | "PARTIAL" | "ORPHANED";

/** One asset row from the plane reconciler (item-level checker output). */
export interface ReconcileAsset {
  documentId: string;
  state: ConsistencyState;
  reasons: string[];
  chunkNodes: number;
  hasCatalogue: boolean;
  hasEmbeddings: boolean;
}

export interface ReconcileReport {
  projectId: string;
  catalogueAvailable: boolean;
  counts: Record<ConsistencyState, number>;
  assetsSampled: number;
  assets: ReconcileAsset[];
  orphanedVectorRows: number;
  orphanedChunkNodes: number;
}

/** Pending graph-extension proposal (agent-initiated write, needs human). */
export interface GraphProposal {
  proposalId: string;
  graphId: string;
  kind: string;
  status: string;
  createdAt: string;
  actor: string;
  summary: string;
}

/** One lineage chain node (ancestry/descendants of a knowledge artifact). */
export interface LineageNodeRow {
  artifactId: string;
  artifactType: string;
  title: string;
  edgeKind: string;
  createdAt: string;
}

export interface LineageReport {
  artifactId: string;
  ancestors: LineageNodeRow[];
  descendants: LineageNodeRow[];
}
