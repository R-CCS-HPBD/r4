/**
 * C3: shared token/cost/context chip text for a session/cost payload.
 * TS surface port — identical formatting across webview / v3 / tui-js.
 */
import type { CostStatePayload } from '@/types/protocol';

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtUsd(v: number): string {
  return v >= 1 ? v.toFixed(2) : v.toFixed(4);
}

/** "⚡ 12.3k (34% ctx) · $0.0931" | "⚡ 12.3k (34% ctx) · free" */
export function formatCostChip(cost: CostStatePayload): string {
  const used = cost.context_used_tokens || cost.turn_tokens || 0;
  const limit = cost.context_limit_tokens || 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const costPart = cost.price_known
    ? ` · $${fmtUsd(Math.max(0, cost.cost_usd_turn))}`
    : ' · free';
  return `⚡ ${fmtTokens(used)} (${pct}% ctx)${costPart}`;
}

/** Long-form tooltip: cumulative + per-turn + model. */
export function formatCostTooltip(cost: CostStatePayload): string {
  const parts = [`${cost.model || 'default model'}`];
  parts.push(`turn ${cost.turn_tokens.toLocaleString()} tok`);
  parts.push(`session ${cost.total_tokens.toLocaleString()} tok`);
  if (cost.price_known) {
    parts.push(`turn $${cost.cost_usd_turn.toFixed(4)}`);
    parts.push(`session $${cost.cost_usd_total.toFixed(4)}`);
  }
  return parts.join(' · ');
}
