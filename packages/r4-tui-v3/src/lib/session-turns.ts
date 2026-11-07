/**
 * `session.turns` rows as text (T5 topic-20): the server-stored shape, no
 * fabricated summaries. Pure, so the VS Code host prints them the same way
 * (ADR-103 D-3); `bridge/session-bridge.ts` re-exports it.
 */

function textOf(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

/** One turn-index entry (UMRS companion_sessions._turn_entries: turn_id,
 * index, role, committed_at): `  #3 t-abc · assistant · 2026-11-01T10:00Z`.
 * Entries carry no model or content; the old renderer read those and showed
 * only the id (TUI audit 2026-10-17). */
export function sessionTurnLine(row: Record<string, unknown>): string {
  const tid = textOf(row.turn_id ?? row.id) || "?";
  const index = typeof row.index === "number" ? `#${row.index} ` : "";
  const parts = [`${index}${tid}`, textOf(row.role), textOf(row.committed_at)];
  return `  ${parts.filter(Boolean).join(" · ")}`;
}

/** The rows of a `session.turns` answer (`rows`, else `turns`). */
export function turnRowsOf(result: { rows?: unknown; turns?: unknown }): Array<Record<string, unknown>> {
  const rows = Array.isArray(result.rows) ? result.rows : result.turns;
  return Array.isArray(rows) ? (rows as Array<Record<string, unknown>>) : [];
}
