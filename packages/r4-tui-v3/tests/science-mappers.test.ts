/**
 * Tests for the ADR-031 server-spine mappers: wire payloads → typed rows.
 * The mappers are the ONLY translation seam; pins the honest-absence rules
 * (links null, stats nulls) and the server-state vocabulary pass-through.
 */

import { describe, expect, test } from "bun:test";
import {
  mapCatalogLinks,
  mapCatalogRead,
  mapCatalogSearch,
  mapCatalogStats,
  mapGraphContent,
  mapGraphList,
  mapGraphProposal,
  mapLineageReport,
  mapReconcileReport,
} from "../src/adapters/science-mappers";

describe("catalogue plane mappers", () => {
  const wireRow = {
    s3_key: "proj-1/xrd/run01.csv",
    state: "ready",
    summary: "XRD scan",
    deterministic_summary: "csv 14 KiB",
    detected_modality: "structured",
    tags: ["xrd"],
    relevance_score: 0.5,
    links: {
      document_id: "ragdoc-proj-1-run01",
      link_method: "s3_object",
      chunk_nodes: 4,
      vector_rows: [{ model: "gte-multilingual-base", dimension: 768 }, { model: null }],
    },
  };

  test("maps one catalogue row incl. links block", () => {
    const mapped = mapCatalogSearch({
      query: "xrd",
      project_id: "proj-1",
      results: [wireRow],
    });
    expect(mapped.projectId).toBe("proj-1");
    expect(mapped.results).toHaveLength(1);
    const row = mapped.results[0];
    expect(row.s3Key).toBe("proj-1/xrd/run01.csv");
    expect(row.modality).toBe("structured");
    expect(row.links?.documentId).toBe("ragdoc-proj-1-run01");
    expect(row.links?.chunkNodes).toBe(4);
    expect(row.links?.vectorRows).toHaveLength(2);
    expect(row.links?.vectorRows[1]?.model).toBeNull();
  });

  test("links null → null, never an empty-link fabrication", () => {
    expect(mapCatalogLinks(null)).toBeNull();
    expect(mapCatalogLinks({})).toBeNull();
    const row = mapCatalogSearch({ query: "", project_id: "p", results: [{ ...wireRow, links: null }] });
    expect(row.results[0]?.links).toBeNull();
  });

  test("catalog stats keep null plane counts (honest absence on old servers)", () => {
    const stats = mapCatalogStats({
      project_id: "proj-1",
      object_count: 3,
      states: { ready: 2, pending: 1 },
      modalities: { structured: 3 },
      canonical_documents: null,
      chunk_nodes: null,
      vector_rows: null,
    });
    expect(stats.canonicalDocuments).toBeNull();
    expect(stats.vectorRows).toBeNull();
  });

  test("catalog stats carry plane counts when the server ships them", () => {
    const stats = mapCatalogStats({
      project_id: "proj-1",
      object_count: 3,
      states: {},
      modalities: {},
      canonical_documents: 4,
      chunk_nodes: 18,
      vector_rows: 12,
    });
    expect(stats.canonicalDocuments).toBe(4);
    expect(stats.chunkNodes).toBe(18);
    expect(stats.vectorRows).toBe(12);
  });

  test("read payload maps range + extraction source + links", () => {
    const read = mapCatalogRead({
      s3_key: "proj-1/a.csv",
      offset: 10,
      length: 500,
      total_size: 900,
      truncated: true,
      text: "body",
      read_source: "extracted",
      links: wireRow.links,
    });
    expect(read.offset).toBe(10);
    expect(read.truncated).toBe(true);
    expect(read.readSource).toBe("extracted");
    expect(read.links?.documentId).toBe("ragdoc-proj-1-run01");
  });
});

describe("graph registry mappers (0069)", () => {
  test("list maps registry rows", () => {
    const graphs = mapGraphList({
      graphs: [
        {
          graph_id: "g-1",
          name: "proj-1",
          description: "Default knowledge graph for project proj-1",
          owner_id: "",
          project_id: "proj-1",
          kind: "project",
          status: "active",
          created_at: "2026-09-09T00:00:00",
        },
      ],
    });
    expect(graphs).toHaveLength(1);
    expect(graphs[0]?.graphId).toBe("g-1");
    expect(graphs[0]?.kind).toBe("project");
  });

  test("content maps id/name/type aliases and edges", () => {
    const content = mapGraphContent({
      graph_id: "g-1",
      name: "proj-1",
      kind: "project",
      project_id: "proj-1",
      nodes: [
        { id: "n1", node_id: "n1", name: "H7", label: "H7", node_type: "hypothesis", project_id: "proj-1", graph_id: "g-1" },
      ],
      edges: [{ from: "n1", to: "n2", relation_type: "tested_by", label: "tested_by", type: "tested_by" }],
      truncated: true,
    });
    expect(content.nodes[0]?.type).toBe("hypothesis");
    expect(content.edges[0]?.type).toBe("tested_by");
    expect(content.truncated).toBe(true);
  });

  test("proposal row maps pending inbox entries", () => {
    const proposal = mapGraphProposal({
      proposal_id: "p-1",
      graph_id: "g-1",
      kind: "add_edge",
      status: "pending",
      created_at: "2026-09-09T00:00:00",
      actor: "agent",
      summary: "agent proposes H8 tested_by E19",
    });
    expect(proposal.proposalId).toBe("p-1");
    expect(proposal.status).toBe("pending");
    expect(proposal.actor).toBe("agent");
  });
});

describe("reconcile mapper (ADR-031 Invariant 4 read-side)", () => {
  test("maps item-level asset states with server vocabulary", () => {
    const report = mapReconcileReport({
      project_id: "proj-1",
      catalogue_available: true,
      counts: { COMPLETE: 2, EMBEDDING_PENDING: 1, PARTIAL: 1, ORPHANED: 0 },
      assets_sampled: 4,
      assets: [
        {
          document_id: "ragdoc-a",
          state: "EMBEDDING_PENDING",
          reasons: ["no embedding rows for chunks (non-error)"],
          chunk_nodes: 3,
          has_catalogue: true,
          has_embeddings: false,
        },
      ],
      orphaned_vector_rows: 0,
      orphaned_chunk_nodes: 2,
    });
    expect(report.counts.EMBEDDING_PENDING).toBe(1);
    const asset = report.assets[0];
    expect(asset?.state).toBe("EMBEDDING_PENDING");
    expect(asset?.reasons[0]).toContain("non-error");
    expect(report.orphanedChunkNodes).toBe(2);
  });

  test("unknown server state falls to PARTIAL, not dropped", () => {
    const report = mapReconcileReport({
      project_id: "p",
      counts: {},
      assets: [{ document_id: "d", state: "FUTURE_STATE", reasons: [] }],
    });
    expect(report.assets[0]?.state).toBe("PARTIAL");
  });
});

describe("lineage mapper (workstream §7)", () => {
  test("maps ancestors/descendants; empty chain stays empty", () => {
    const lineage = mapLineageReport({
      artifact_id: "art-1",
      ancestors: [
        { artifact_id: "a2l-1", artifact_type: "a2l", title: "upload batch", edge_kind: "derived_from", created_at: "2026-09-01T00:00:00" },
      ],
      descendants: [],
    });
    expect(lineage.ancestors).toHaveLength(1);
    expect(lineage.ancestors[0]?.edgeKind).toBe("derived_from");
    expect(lineage.descendants).toHaveLength(0);
  });
});
