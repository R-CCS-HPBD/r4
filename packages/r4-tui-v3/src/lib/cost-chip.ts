/**
 * C3: shared token/cost/context chip text — v3 port (identical rules to the
 * webview's lib/cost-chip.ts; keep in sync).
 */
import type { CostState } from "../types";

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtUsd(v: number): string {
  return v >= 1 ? v.toFixed(2) : v.toFixed(4);
}

/** "⚡ 12.3k (34% ctx) · $0.0931" | "⚡ 12.3k (34% ctx) · free" */
export function formatCostChip(cost: CostState): string {
  const used = cost.context_used_tokens || cost.turn_tokens || 0;
  const limit = cost.context_limit_tokens || 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const costPart = cost.price_known
    ? ` · $${fmtUsd(Math.max(0, cost.cost_usd_turn))}`
    : " · free";
  return `⚡ ${fmtTokens(used)} (${pct}% ctx)${costPart}`;
}

/**
 * ADR-037: tool-schema context suffix — " · tools ~3.1k" when the latest
 * selection record reports an after-estimate, "" otherwise. Honesty rule:
 * absence of a record means absence of the suffix, never a 0.
 */
export function formatToolSchemaSuffix(schemaTokensAfter: number | undefined): string {
  if (!schemaTokensAfter || schemaTokensAfter <= 0) return "";
  return ` · tools ~${fmtTokens(schemaTokensAfter)}`;
}

/** Client-measured time-to-first-token suffix for the turn chip ("" when absent). */
export function formatTtftSuffix(ttftMs: number | null | undefined): string {
  if (ttftMs == null || !Number.isFinite(ttftMs) || ttftMs < 0) return "";
  const label = ttftMs < 1000 ? `${Math.round(ttftMs)}ms` : `${(ttftMs / 1000).toFixed(1)}s`;
  return ` · ttft ${label}`;
}
