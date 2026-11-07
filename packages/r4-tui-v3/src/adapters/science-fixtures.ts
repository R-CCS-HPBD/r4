/**
 * Shared science fixture matrix (knowledge-catalogue UX workstream §13).
 *
 * Ten scenarios in real UMRS wire shapes, keyed by a stable scenario name so
 * the CLI/TUI/webview parity tests pin the SAME vocabulary on every surface.
 * These flows are read-side fixtures: they exercise how a surface *renders*
 * server state — they never replace server state itself.
 */

import type { ConsistencyState } from "../types/science";

export interface ScienceFixtureScenario {
  /** Stable cross-surface key (§13 matrix row). */
  name:
    | "catalogue-only"
    | "doc-no-nodes"
    | "nodes-no-embeddings"
    | "fully-searchable"
    | "embedding-pending"
    | "partial"
    | "orphaned"
    | "legacy-corpus-only"
    | "provenance-incomplete"
    | "identity-migration";
  /** Catalogue search row (null for corpus-only lanes). */
  catalogRow: Record<string, unknown> | null;
  /** Reconcile projection row for the same document, when one exists. */
  reconcileRow: Record<string, unknown> | null;
  /** Expected badge state the parity tests assert on every surface. */
  expectedState: ConsistencyState | "NO_MINT" | "UNSAMPLED";
}

const baseLinks = {
  link_method: "s3_object",
  chunk_nodes: 4,
  vector_rows: [{ model: "gte-multilingual-base", dimension: 768 }],
};

export const SCIENCE_FIXTURES: readonly ScienceFixtureScenario[] = [
  {
    name: "catalogue-only",
    catalogRow: {
      s3_key: "proj/raw/upload.csv",
      state: "ready",
      summary: "raw upload, never canonicalized",
      detected_modality: "structured",
      tags: [],
      relevance_score: 0.1,
      links: null,
    },
    reconcileRow: null,
    expectedState: "NO_MINT",
  },
  {
    name: "doc-no-nodes",
    catalogRow: {
      s3_key: "proj/docs/bare.pdf",
      state: "ready",
      summary: "minted document, zero chunk nodes",
      detected_modality: "document",
      tags: [],
      relevance_score: 0.2,
      links: { document_id: "ragdoc-bare", ...baseLinks, chunk_nodes: 0, vector_rows: [] },
    },
    reconcileRow: {
      document_id: "ragdoc-bare",
      state: "PARTIAL",
      reasons: ["canonical document has zero chunk nodes"],
      chunk_nodes: 0,
      has_catalogue: true,
      has_embeddings: false,
    },
    expectedState: "PARTIAL",
  },
  {
    name: "nodes-no-embeddings",
    catalogRow: {
      s3_key: "proj/docs/notes.md",
      state: "ready",
      summary: "canonical chunks exist, vectors pending",
      detected_modality: "text",
      tags: [],
      relevance_score: 0.3,
      links: { document_id: "ragdoc-notes", ...baseLinks, vector_rows: [] },
    },
    reconcileRow: {
      document_id: "ragdoc-notes",
      state: "EMBEDDING_PENDING",
      reasons: ["no embedding rows for chunks (non-error)"],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: false,
    },
    expectedState: "EMBEDDING_PENDING",
  },
  {
    name: "fully-searchable",
    catalogRow: {
      s3_key: "proj/xrd/run01.csv",
      state: "ready",
      summary: "complete across all three planes",
      detected_modality: "structured",
      tags: ["xrd"],
      relevance_score: 0.9,
      links: { document_id: "ragdoc-run01", ...baseLinks },
    },
    reconcileRow: {
      document_id: "ragdoc-run01",
      state: "COMPLETE",
      reasons: [],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: true,
    },
    expectedState: "COMPLETE",
  },
  {
    name: "embedding-pending",
    catalogRow: {
      s3_key: "proj/docs/new.md",
      state: "ready",
      summary: "transient embed failure — must NOT downgrade the write",
      detected_modality: "text",
      tags: [],
      relevance_score: 0.4,
      links: { document_id: "ragdoc-new", ...baseLinks, vector_rows: [] },
    },
    reconcileRow: {
      document_id: "ragdoc-new",
      state: "EMBEDDING_PENDING",
      reasons: ["no embedding rows for chunks (non-error)"],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: false,
    },
    expectedState: "EMBEDDING_PENDING",
  },
  {
    name: "partial",
    catalogRow: {
      s3_key: "proj/docs/half.pdf",
      state: "ready",
      summary: "catalogue row with canonical mint missing",
      detected_modality: "document",
      tags: [],
      relevance_score: 0.4,
      links: null,
    },
    reconcileRow: null,
    expectedState: "NO_MINT",
  },
  {
    name: "orphaned",
    catalogRow: {
      s3_key: "proj/docs/stale.md",
      state: "ready",
      summary: "minted document whose parent rows were removed",
      detected_modality: "text",
      tags: [],
      relevance_score: 0.2,
      links: { document_id: "ragdoc-stale", ...baseLinks },
    },
    reconcileRow: {
      document_id: "ragdoc-stale",
      state: "ORPHANED",
      reasons: ["vector row with no node"],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: true,
    },
    expectedState: "ORPHANED",
  },
  {
    name: "legacy-corpus-only",
    // Legacy corpus rows may surface in retrieval but never mint canonical
    // identity (Invariant 2) — surfaces render them visibly subordinate.
    catalogRow: {
      s3_key: "legacy/corpus/chunk-991",
      state: "legacy",
      summary: "legacy rag corpus chunk (retrieval lane only)",
      detected_modality: "text",
      tags: ["legacy"],
      relevance_score: 0.1,
      links: null,
    },
    reconcileRow: null,
    expectedState: "NO_MINT",
  },
  {
    name: "provenance-incomplete",
    // Lineage projection empty for a minted document — "no recorded chain",
    // never rendered as "reproducible".
    catalogRow: {
      s3_key: "proj/docs/noprov.md",
      state: "ready",
      summary: "canonical document with no recorded lineage",
      detected_modality: "text",
      tags: [],
      relevance_score: 0.3,
      links: { document_id: "ragdoc-noprov", ...baseLinks },
    },
    reconcileRow: {
      document_id: "ragdoc-noprov",
      state: "COMPLETE",
      reasons: [],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: true,
    },
    expectedState: "COMPLETE",
  },
  {
    name: "identity-migration",
    // ragdoc-legacy-* lane of §identity migration: the canonical id exists
    // but the catalogue link may still point at the pre-migration key.
    catalogRow: {
      s3_key: "proj/docs/migrated.md",
      state: "ready",
      summary: "document id migrated to the legacy-prefixed lane",
      detected_modality: "text",
      tags: ["migrated"],
      relevance_score: 0.3,
      links: { document_id: "ragdoc-legacy-migrated", ...baseLinks, link_method: "filename" },
    },
    reconcileRow: {
      document_id: "ragdoc-legacy-migrated",
      state: "COMPLETE",
      reasons: [],
      chunk_nodes: 4,
      has_catalogue: true,
      has_embeddings: true,
    },
    expectedState: "COMPLETE",
  },
] as const;
