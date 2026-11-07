/**
 * mgmt-card.v1 formatters — webview port of packages/r4-tui-v3/src/lib/
 * mgmt-card-format.ts (pure functions; identical mapping rules so the three
 * surfaces render the same content from the same server payloads).
 */
import type {
  KnowledgeGraphEntry,
  KnowledgeInventory,
  KnowledgeStatus,
  LoopListResponse,
  LoopStatusResponse,
  MgmtRow,
  SessionViewResponse,
  WfListResponse,
  WfRunDetail,
} from '@/types/protocol';

export type MgmtCardKind =
  | 'wf-list'
  | 'wf-show'
  | 'wf-run'
  | 'wf-results'
  | 'loop-list'
  | 'loop-status'
  | 'session-view'
  | 'knowledge-status'
  | 'knowledge-detail'
  | 'knowledge-graph'
  | 'request-op';

function trunc(value: string, max: number): string {
  if (max <= 0) return '';
  if (value.length <= max) return value;
  if (max === 1) return '…';
  return `${value.slice(0, max - 1)}…`;
}

/** Status markers consistent across all surfaces. */
export function statusIcon(status: string | undefined): string {
  const s = (status ?? '').toLowerCase();
  if (s === 'failed' || s === 'error') return '✗';
  if (s === 'running' || s === 'in_progress' || s === 'pending') return '▶';
  if (s.startsWith('succ') || s === 'done' || s === 'ok' || s === 'completed') return '✓';
  if (s === 'cancelled' || s === 'canceled' || s === 'stopped') return '■';
  return '•';
}

/** [name, version-tag, one-line description] rows for a workflow list card. */
export function wfListRows(payload: unknown): { rows: string[][]; total: number } {
  const p = (payload ?? {}) as WfListResponse;
  const items = p.workflows ?? [];
  const rows = items.slice(0, 10).map((wf) => [
    trunc(String(wf.name ?? wf.workflow_name ?? ''), 36),
    String(wf.tags?.[0] ?? ''),
    trunc(String(wf.description ?? ''), 48),
  ]);
  return { rows, total: p.count ?? items.length };
}

/** Key/value header rows for a workflow run/status card. */
export function wfStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === '') return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push('execution', p.execution_id);
  push('workflow', p.workflow_name);
  push('status', p.status);
  if (typeof p.progress === 'number') push('progress', `${Math.round(p.progress * 100)}%`);
  push('error', p.error);
  return rows;
}

export type WfStepRow = { name: string; status: string; duration?: string };

/** Steps inside a wf-run card (from the execution payload's `steps` list). */
export function wfDetailSteps(payload: unknown): { header: MgmtRow[]; steps: WfStepRow[] } {
  const p = (payload ?? {}) as { execution?: WfRunDetail & { steps?: Array<Record<string, unknown>> } };
  const exec = (p.execution ?? {}) as Record<string, unknown>;
  const header = wfStatusRows(exec);
  const specs = (exec.steps ?? exec.step_results ?? exec.attempts ?? []) as Array<Record<string, unknown>>;
  const steps: WfStepRow[] = (Array.isArray(specs) ? specs : [])
    .slice(0, 12)
    .map((step, idx) => ({
      name: String(step.name ?? step.step_name ?? step.id ?? `step ${idx + 1}`),
      status: String(step.status ?? step.state ?? ''),
      duration:
        typeof step.duration_ms === 'number' ? `${Math.round(step.duration_ms / 1000)}s` : undefined,
    }));
  return { header, steps };
}

/** WF results ≈ status rows + artifact/output counters. */
export function wfResultsRows(payload: unknown): MgmtRow[] {
  const rows = wfStatusRows(payload);
  const p = (payload ?? {}) as { artifacts?: unknown; outputs?: unknown };
  if (Array.isArray(p.artifacts)) rows.push({ key: 'artifacts', value: `${p.artifacts.length} item(s)` });
  if (Array.isArray(p.outputs)) rows.push({ key: 'outputs', value: `${p.outputs.length} item(s)` });
  return rows;
}

/** [loop_id, status, i/max, goal] rows for the loop-list card. */
export function loopListRows(payload: unknown): { rows: string[][]; active: number; runtime?: string } {
  const p = (payload ?? {}) as LoopListResponse;
  const items = p.loops ?? [];
  const rows = items.slice(0, 10).map((loop) => [
    String(loop.loop_id ?? '?'),
    String(loop.status ?? ''),
    `${String(loop.current_iteration ?? 0)}/${String(loop.max_iterations ?? '∝')}`,
    trunc(String(loop.research_goal ?? ''), 44),
  ]);
  const active = p.active_count ?? 0;
  return { rows, active, runtime: p.runtime ?? undefined };
}

/** Detail rows for a single loop. */
export function loopStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as LoopStatusResponse;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === '') return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push('loop', p.loop_id);
  push('status', p.status);
  if (p.current_iteration !== undefined || p.max_iterations !== undefined) {
    push('iterations', `${String(p.current_iteration ?? 0)}/${String(p.max_iterations ?? '∝')}`);
  }
  push('phase', p.phase);
  push('project', p.project_id);
  push('runtime', p.runtime);
  push('created', p.created_at);
  push('error', p.error_message);
  return rows;
}

/** [iteration tag, one-line summary] rows for a loop's recent iterations. */
export function loopIterationRows(payload: unknown): string[][] {
  const p = (payload ?? {}) as LoopStatusResponse;
  const its = p.recent_iterations ?? [];
  return its.slice(0, 5).map((it, idx) => {
    const iter = it.iteration ?? idx + 1;
    const when = it.finished_at ?? it.started_at ?? '';
    const result = trunc(String(it.result_summary ?? it.hypothesis ?? it.status ?? ''), 60);
    return [`#${iter}${when ? ` · ${when}` : ''}`, result];
  });
}

/** Session-view rows: loops + knowledge digest + authority location. */
export function sessionViewRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as SessionViewResponse;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === '') return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  if (p.session_id) push('session', p.session_id);
  if (p.project_id) push('project', p.project_id);
  const loops = Array.isArray(p.loops) ? p.loops : [];
  if (loops.length) {
    push('loops', `${loops.length} registered`);
  }
  const digest = (p as { knowledge_digest?: Record<string, unknown> }).knowledge_digest;
  if (digest && typeof digest === 'object') {
    const nodes = (digest as Record<string, unknown>).nodes ?? (digest.nodes as Record<string, unknown> | undefined);
    if (nodes && typeof nodes === 'object') {
      const total = Object.values(nodes as Record<string, number>).reduce((a, b) => a + (Number(b) || 0), 0);
      push('knowledge', `${total} node(s)`);
    }
  }
  push('authority', p.authority_note ?? undefined);
  return rows;
}

/** Knowledge-graph status rows (nodes by label, edges, recent activity). */
export function knowledgeStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as KnowledgeStatus;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === '') return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  const nodes = p.nodes_by_label ?? {};
  const totalNodes = Object.values(nodes).reduce((a, b) => a + (Number(b) || 0), 0);
  push('nodes', totalNodes);
  for (const [label, count] of Object.entries(nodes).slice(0, 4)) {
    push(`· ${label}`, count);
  }
  push('edges', p.edges ?? 0);
  push('last ingested', p.last_ingest_summary);
  if (p.growth_since_last !== undefined) push('growth', `+${String(p.growth_since_last)}`);
  push('runtime', p.runtime);
  return rows;
}

/** Session-local inventory numbers for the detail card. */
export function knowledgeInventoryRows(payload: unknown): KnowledgeInventory {
  const p = (payload ?? {}) as KnowledgeInventory;
  return {
    nodes: p.nodes ?? 0,
    edges: p.edges ?? 0,
    patterns: p.patterns ?? 0,
    lessons: p.lessons ?? 0,
    backend: p.backend ?? null,
  };
}

/** knowledge-graph card rows (name, kind, owner, description). */
export function knowledgeGraphListRows(payload: unknown): string[][] {
  const entries = ((payload ?? {}) as { graphs?: KnowledgeGraphEntry[] }).graphs ?? [];
  return entries.slice(0, 8).map((g) => [
    trunc(String(g.name ?? g.graph_id ?? ''), 20),
    String(g.kind ?? ''),
    trunc(String(g.owner ?? ''), 14),
    trunc(String(g.description ?? ''), 30),
  ]);
}
