/**
 * /evidence slash family (sweep batch 3, 2026-09-27): the V-07 evidence
 * plane (claims, retractions, negative results) — caller-scoped rows.
 * Bridge verb evidence.list.
 */

import { getCompanionBridge } from "../bridge";
import { bridgeErrorHint } from "../bridge/jsonl-client";
import { actions } from "../stores";
import { isOfflineBackend } from "../lib/console-mode";

export type EvidenceDispatchResult = { kind: "handled" };

// UMRS evidence._to_response: evidence_id, claim_id, content, citation, …
// (no id/claim keys — every row printed "?" and only the claim id; TUI audit
// 2026-10-17). The old keys stay as fallbacks.
interface EvidenceRow {
  evidence_id?: string;
  id?: string;
  claim_id?: string;
  claim?: string;
  content?: string;
  citation?: string;
  status?: string;
  source_type?: string;
  created_at?: string;
}

function _rowText(row: EvidenceRow): string {
  return row.content || row.citation || row.claim || row.claim_id || "?";
}

function _rowLine(row: EvidenceRow): string {
  // Full id (evd-<12 hex>): a cut one cannot be pasted into a retract.
  const id = row.evidence_id ?? row.id ?? "?";
  const claim = _rowText(row).replace(/\s+/g, " ").slice(0, 72);
  const bits = [`  ${id}`, row.status ?? "?", claim];
  if (row.source_type) bits.push(`[${row.source_type}]`);
  return bits.join(" · ");
}

export async function handleEvidenceCommand(args: string): Promise<EvidenceDispatchResult> {
  if (isOfflineBackend()) {
    actions.sendSystemNotice("evidence: requires a live backend");
    return { kind: "handled" };
  }
  const status = args.trim() || undefined;
  const bridge = getCompanionBridge();
  try {
    const out = (await bridge.request(
      "evidence.list",
      status ? { status, limit: 30 } : { limit: 30 },
      15_000,
    )) as { evidence?: EvidenceRow[]; count?: number };
    const rows = Array.isArray(out.evidence) ? out.evidence : [];
    if (rows.length === 0) {
      actions.sendSystemNotice(
        status
          ? `evidence: no rows with status "${status}" — plane is honest, never sampled`
          : "evidence: no rows yet — claims land here as the loop produces them",
      );
      return { kind: "handled" };
    }
    actions.sendSystemNotice(
      `evidence (${out.count ?? rows.length}):\n${rows.slice(0, 30).map(_rowLine).join("\n")}`,
    );
  } catch (err) {
    // The server's remedy rides on its own line (signed out: "/connect …").
    const hint = bridgeErrorHint(err);
    actions.sendSystemNotice(
      `evidence failed: ${err instanceof Error ? err.message : String(err)}${hint ? `\n  → ${hint}` : ""}`,
    );
  }
  return { kind: "handled" };
}
