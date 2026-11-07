/**
 * Consistency badge join rules (ADR-031 Invariant 4 read-side).
 * The badge is the reconciler's projection joined on links.documentId —
 * never locally derived. Pins: minted-absent ≠ error, EMBEDDING_PENDING ≠
 * failure, unsampled ≠ unminted.
 */

import { describe, expect, test } from "bun:test";
import { consistencyBadge, reconcileAssetFor, reconcileCountsLine } from "../src/lib/consistency";
import type { CatalogLinks, ReconcileAsset, ReconcileReport } from "../src/types/science";

const links: CatalogLinks = {
  documentId: "ragdoc-run01",
  linkMethod: "s3_object",
  chunkNodes: 4,
  vectorRows: [{ model: "gte-multilingual-base", dimension: 768 }],
};

function report(assets: ReconcileAsset[]): ReconcileReport {
  return {
    projectId: "proj-1",
    catalogueAvailable: true,
    counts: { COMPLETE: 1, EMBEDDING_PENDING: 1, PARTIAL: 0, ORPHANED: 0 },
    assetsSampled: assets.length,
    assets,
    orphanedVectorRows: 0,
    orphanedChunkNodes: 0,
  };
}

const completeAsset: ReconcileAsset = {
  documentId: "ragdoc-run01",
  state: "COMPLETE",
  reasons: [],
  chunkNodes: 4,
  hasCatalogue: true,
  hasEmbeddings: true,
};

describe("consistency badge", () => {
  test("reconcile row wins: ✓ COMPLETE with server label", () => {
    const badge = consistencyBadge({ links }, report([completeAsset]));
    expect(badge.glyph).toBe("✓");
    expect(badge.state).toBe("COMPLETE");
  });

  test("EMBEDDING_PENDING renders ◐ and stays non-error", () => {
    const pending: ReconcileAsset = {
      ...completeAsset,
      state: "EMBEDDING_PENDING",
      reasons: ["no embedding rows for chunks (non-error)"],
      hasEmbeddings: false,
    };
    const badge = consistencyBadge({ links }, report([pending]));
    expect(badge.glyph).toBe("◐");
    expect(badge.label).toContain("non-error");
  });

  test("no mint = · (existence plane), never an error state", () => {
    const badge = consistencyBadge({ links: null }, report([]));
    expect(badge.glyph).toBe("·");
    expect(badge.state).toBe("NO_MINT");
    expect(badge.label).toBe("no canonical document minted");
  });

  test("minted but outside the reconcile sample = …, distinct from NO_MINT", () => {
    const badge = consistencyBadge({ links }, report([]));
    expect(badge.glyph).toBe("…");
    expect(badge.state).toBe("UNSAMPLED");
    expect(badge.state).not.toBe("NO_MINT");
  });

  test("links null cannot join reconcile → NO_MINT even with a report", () => {
    // No document_id anchor on the catalogue row = no join is possible;
    // the reconciler's per-asset row cannot be claimed by this asset.
    const badge = consistencyBadge({ links: null }, report([completeAsset]));
    expect(badge.state).toBe("NO_MINT");
  });

  test("no report at all → honest unknown, not fabricated COMPLETE", () => {
    const badge = consistencyBadge({ links }, null);
    expect(badge.state).toBe("UNSAMPLED");
  });

  test("join keys on documentId only", () => {
    expect(reconcileAssetFor({ links }, report([completeAsset]))?.documentId).toBe("ragdoc-run01");
    expect(reconcileAssetFor({ links: { ...links, documentId: "other" } }, report([completeAsset]))).toBeNull();
  });

  test("counts line carries all four states + loose rows", () => {
    const line = reconcileCountsLine({ ...report([]), orphanedChunkNodes: 2 });
    expect(line).toContain("✓ 1");
    expect(line).toContain("◐ 1 pending");
    expect(line).toContain("▲ 0 partial");
    expect(line).toContain("✗ 0 orphaned");
    expect(line).toContain("loose rows 2");
  });

  test("no report → no line (never invent numbers)", () => {
    expect(reconcileCountsLine(null)).toBeNull();
  });
});
