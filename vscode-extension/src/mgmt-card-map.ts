/**
 * M5 host-side card query map — pure module (no vscode imports) so the
 * unit test can load it under plain node. Mirrors the bridge methods the
 * TUI slash verbs use exactly (same names, same param names).
 */
export interface MgmtCardQueryDef {
  method: string;
  /** Required parameter slot filled from the verb argument, when any. */
  argParam?: string;
}

export const MGMT_CARD_QUERY_MAP: Partial<Record<string, MgmtCardQueryDef>> = {
  "wf-list": { method: "workflow.list" },
  "wf-show": { method: "workflow_describe", argParam: "workflow_name" },
  "wf-run": { method: "workflow.detail", argParam: "execution_id" },
  "wf-results": { method: "workflow.results", argParam: "execution_id" },
  "loop-list": { method: "research_loop.list" },
  "loop-status": { method: "research_loop.status", argParam: "loop_id" },
  "session-view": { method: "research_session.view" },
  "knowledge-status": { method: "research_knowledge.status" },
  "knowledge-detail": { method: "get_knowledge" },
  "knowledge-graph": { method: "knowledge_graph_content", argParam: "graph_id" },
};

export const MGMT_KINDS: ReadonlySet<string> = new Set(Object.keys(MGMT_CARD_QUERY_MAP));

/** Build the params record for one query. Throws when the arg is missing. */
export function buildMgmtCardParams(kind: string, arg?: string): Record<string, unknown> {
  const def = MGMT_CARD_QUERY_MAP[kind];
  if (!def) throw new Error(`unknown management kind "${kind}"`);
  if (def.argParam && !arg) {
    throw new Error(`${kind} needs an argument (${def.argParam})`);
  }
  const params: Record<string, unknown> = {};
  if (def.argParam) params[def.argParam] = arg;
  return params;
}
