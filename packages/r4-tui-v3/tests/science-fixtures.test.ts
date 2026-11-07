/**
 * §13 fixture-matrix parity: every scenario in the shared matrix maps through
 * the mapper chain to its pinned badge state. A surfaced row must resolve to
 * the same semantic state on CLI/TUI/webview — this file is the TUI half of
 * that contract (r4/tests/test_data_catalog_cli_matrix.py is the CLI half).
 */

import { describe, expect, test } from "bun:test";
import { SCIENCE_FIXTURES } from "../src/adapters/science-fixtures";
import { mapCatalogAsset, mapReconcileAsset } from "../src/adapters/science-mappers";
import { consistencyBadge } from "../src/lib/consistency";
import type { ReconcileReport } from "../src/types/science";

describe("science fixture matrix (§13)", () => {
  test("matrix covers all ten workstream states", () => {
    const names: string[] = SCIENCE_FIXTURES.map((f) => f.name).sort();
    expect(names).toEqual(
      [
        "catalogue-only",
        "doc-no-nodes",
        "nodes-no-embeddings",
        "fully-searchable",
        "embedding-pending",
        "partial",
        "orphaned",
        "legacy-corpus-only",
        "provenance-incomplete",
        "identity-migration",
      ].sort(),
    );
  });

  for (const fixture of SCIENCE_FIXTURES) {
    test(`${fixture.name} resolves to ${fixture.expectedState}`, () => {
      const asset = fixture.catalogRow ? mapCatalogAsset(fixture.catalogRow) : null;
      const reconcile = fixture.reconcileRow ? mapReconcileAsset(fixture.reconcileRow) : null;
      const report: ReconcileReport | null = reconcile
        ? {
            projectId: "proj",
            catalogueAvailable: true,
            counts: { COMPLETE: 0, EMBEDDING_PENDING: 0, PARTIAL: 0, ORPHANED: 0 },
            assetsSampled: 1,
            assets: [reconcile],
            orphanedVectorRows: 0,
            orphanedChunkNodes: 0,
          }
        : null;
      const badge = consistencyBadge(asset ?? { links: null }, report);
      expect(badge.state).toBe(fixture.expectedState);
    });
  }

  test("legacy-corpus-only carries no canonical identity", () => {
    const legacy = SCIENCE_FIXTURES.find((f) => f.name === "legacy-corpus-only")!;
    const asset = mapCatalogAsset(legacy.catalogRow!);
    expect(asset.links).toBeNull();
    expect(asset.state).toBe("legacy");
  });

  test("provenance-incomplete keeps COMPLETE badge — lineage absence is orthogonal", () => {
    const fixture = SCIENCE_FIXTURES.find((f) => f.name === "provenance-incomplete")!;
    expect(fixture.expectedState).toBe("COMPLETE");
  });
});
