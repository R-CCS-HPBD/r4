/**
 * Map knowledge verify API payloads to VerificationResult store shape.
 */

import type { VerificationResult } from "../types";

/** First array-valued field wins, mapped to strings; empty array still wins. */
function asStringArray(...values: unknown[]): string[] {
  for (const value of values) {
    if (Array.isArray(value)) return value.map(String);
  }
  return [];
}

function evidenceItemId(item: unknown): string {
  if (!item || typeof item !== "object") return "";
  const row = item as Record<string, unknown>;
  return String(row.source_name ?? row.source ?? row.source_id ?? "");
}

function mapEvidenceIds(result: Record<string, unknown>): string[] {
  if (Array.isArray(result.evidenceIds)) return result.evidenceIds.map(String);
  if (Array.isArray(result.evidence_ids)) return result.evidence_ids.map(String);
  if (!Array.isArray(result.evidence)) return [];
  return (result.evidence as unknown[]).map(evidenceItemId).filter(Boolean);
}

export function mapVerifyResponse(
  question: string,
  result: Record<string, unknown>,
): VerificationResult {
  return {
    question,
    answer: String(result.answer ?? result.message ?? "No answer returned"),
    confidence: typeof result.confidence === "number" ? result.confidence : 0,
    reasoningPath: asStringArray(result.reasoningPath, result.reasoning_path),
    evidenceIds: mapEvidenceIds(result),
    graphTraversal: asStringArray(result.graphTraversal, result.graph_traversal),
    supportingPapers: asStringArray(result.supportingPapers, result.supporting_papers),
    supportingExperiments: asStringArray(
      result.supportingExperiments,
      result.supporting_experiments,
    ),
    relatedHypotheses: asStringArray(result.relatedHypotheses, result.related_hypotheses),
  };
}
