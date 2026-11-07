/**
 * Consistency-state surface for the science catalog (ADR-031 Invariant 4).
 *
 * The ONLY authority is the server-side plane reconciler — the store holds
 * its projection (scienceStore.reconcile). These helpers join a catalogue
 * row's `links.documentId` to that projection and pick a glyph. No local
 * re-derivation happens anywhere in this file.
 */

import type { CatalogAsset, ReconcileAsset, ReconcileReport } from "../types/science";

export interface ConsistencyBadge {
  glyph: string;
  state: string;
  /** Short plain-language meaning (never implies vector absence = no knowledge). */
  label: string;
  /** Server reason when PARTIAL/ORPHANED; "" otherwise. */
  reason: string;
}

const STATE_PRESENTATION: Record<string, { glyph: string; label: string }> = {
  COMPLETE: { glyph: "✓", label: "catalogue + canonical + embeddings" },
  EMBEDDING_PENDING: { glyph: "◐", label: "canonical, embeddings pending (non-error)" },
  PARTIAL: { glyph: "▲", label: "partial cross-plane write" },
  ORPHANED: { glyph: "✗", label: "reference without parent row" },
};

/** Find the reconcile asset for one catalogue row's canonical document. */
export function reconcileAssetFor(
  asset: Pick<CatalogAsset, "links">,
  report: ReconcileReport | null,
): ReconcileAsset | null {
  const documentId = asset.links?.documentId;
  if (!documentId || !report) return null;
  return report.assets.find((a) => a.documentId === documentId) ?? null;
}

/**
 * Badge for one catalogue row.
 *
 * - links null → "·" ("no canonical document minted"). No document_id anchor
 *   means no reconcile join is possible; never rendered as an error — the
 *   catalogue is the existence plane and minting is a downstream step.
 */
export function consistencyBadge(
  asset: Pick<CatalogAsset, "links">,
  report: ReconcileReport | null,
): ConsistencyBadge {
  const reconcile = reconcileAssetFor(asset, report);
  if (reconcile) {
    const presentation = STATE_PRESENTATION[reconcile.state] ?? {
      glyph: "?",
      label: reconcile.state,
    };
    return {
      glyph: presentation.glyph,
      state: reconcile.state,
      label: presentation.label,
      reason: reconcile.reasons[0] ?? "",
    };
  }
  if (asset.links) {
    // Minted document not present in this reconcile sample — honest unknown.
    return { glyph: "…", state: "UNSAMPLED", label: "outside reconcile sample", reason: "" };
  }
  return { glyph: "·", state: "NO_MINT", label: "no canonical document minted", reason: "" };
}

/** Summary counts line for the reconcile projection (§8 health strip row). */
export function reconcileCountsLine(report: ReconcileReport | null): string | null {
  if (!report) return null;
  const c = report.counts;
  const parts = [
    `✓ ${c.COMPLETE}`,
    `◐ ${c.EMBEDDING_PENDING} pending`,
    `▲ ${c.PARTIAL} partial`,
    `✗ ${c.ORPHANED} orphaned`,
  ];
  const orphans = report.orphanedVectorRows + report.orphanedChunkNodes;
  if (orphans > 0) parts.push(`(loose rows ${orphans})`);
  return parts.join("  ");
}
