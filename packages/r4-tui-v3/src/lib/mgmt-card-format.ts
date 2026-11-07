/**
 * Pure formatters for management cards (mgmt-card.v1 contract on the v3
 * surface). Keep JSX out of here so each formatter is unit-testable without
 * a renderer.
 */

import { workflowScopeLabel } from "./workflow-sharing";

export type WfListEntry = {
  workflow_name?: string;
  name?: string | null;
  description?: string | null;
  tags?: string[];
  /** Ownership (2026-10-23): `shared` marks a row `owner` shared with the caller. */
  scope?: string;
  owner?: string;
  shared?: boolean;
  access?: string[];
};

export type MgmtRow = { key: string; value: string };

function trunc(value: string, max: number): string {
  const v = value.replace(/\s+/g, " ").trim();
  return v.length > max ? `${v.slice(0, Math.max(0, max - 1))}…` : v;
}

export function statusIcon(status: string | undefined): string {
  const s = (status ?? "").toLowerCase();
  if (s === "succeeded" || s === "success" || s === "done" || s === "ok") return "◆";
  if (s === "running" || s === "in_progress" || s === "waiting") return "▶";
  if (s === "pending" || s === "queued" || s === "created") return "…";
  if (s === "canceled" || s === "cancelled") return "■";
  if (s === "failed" || s === "error") return "✕";
  return "?";
}

export function wfListRows(payload: unknown): { rows: string[][]; total: number } {
  const body = (payload ?? {}) as { workflows?: WfListEntry[]; count?: number };
  const items = Array.isArray(body.workflows) ? body.workflows : [];
  const rows = items.slice(0, 12).map((w) => {
    const name = w.workflow_name ?? w.name ?? "(unnamed)";
    const desc = trunc(w.description ?? "", 48);
    return [name, workflowScopeLabel(w), desc];
  });
  return { rows, total: body.count ?? items.length };
}

export function wfStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push("execution", p.execution_id);
  push("workflow", p.workflow_name);
  push("status", p.status);
  if (typeof p.progress === "number") push("progress", `${Math.round(p.progress * 100)}%`);
  push("error", p.error);
  return rows;
}

export type WfStepRow = { name: string; status: string; duration?: string };

export function wfDetailSteps(payload: unknown): {
  header: MgmtRow[];
  steps: WfStepRow[];
} {
  const p = (payload ?? {}) as { execution?: Record<string, unknown> };
  const exec = (p.execution ?? {}) as Record<string, unknown>;
  const header = wfStatusRows(exec);
  const specs = (exec.steps ?? exec.step_results ?? exec.attempts ?? []) as Array<Record<string, unknown>>;
  const steps: WfStepRow[] = (Array.isArray(specs) ? specs : [])
    .slice(0, 12)
    .map((step, idx) => ({
      name: String(step.name ?? step.step_name ?? step.id ?? `step ${idx + 1}`),
      status: String(step.status ?? step.state ?? ""),
      duration:
        typeof step.duration_ms === "number"
          ? `${Math.round(step.duration_ms / 1000)}s`
          : undefined,
    }));
  return { header, steps };
}

export function wfResultsRows(payload: unknown): MgmtRow[] {
  const rows = wfStatusRows(payload);
  const p = (payload ?? {}) as { artifacts?: unknown; outputs?: unknown };
  if (Array.isArray(p.artifacts)) {
    rows.push({ key: "artifacts", value: `${p.artifacts.length} item(s)` });
  }
  if (Array.isArray(p.outputs)) {
    rows.push({ key: "outputs", value: `${p.outputs.length} item(s)` });
  } else if (typeof p.outputs === "object" && p.outputs !== null) {
    rows.push(...wfOutputsDictRows(p.outputs as Record<string, unknown>));
  }
  return rows;
}

/** UMRS results are `{steps: {...}, final: ...}` (WorkflowResult.outputs is
 * a dict); the array check alone never showed them (TUI audit 2026-10-17). */
function wfOutputsDictRows(outputs: Record<string, unknown>): MgmtRow[] {
  const rows: MgmtRow[] = [];
  const steps = outputs.steps;
  if (typeof steps === "object" && steps !== null) {
    rows.push({ key: "steps", value: `${Object.keys(steps).length} step result(s)` });
  }
  if (outputs.final !== undefined && outputs.final !== null) {
    const text = typeof outputs.final === "string" ? outputs.final : JSON.stringify(outputs.final);
    rows.push({ key: "final", value: trunc(text, 64) });
  }
  return rows;
}

/** Flatten the freeform describe payload into display rows. */
export function loopListRows(payload: unknown): { rows: string[][]; active: number; runtime?: string } {
  const p = (payload ?? {}) as {
    loops?: Array<Record<string, unknown>>;
    active_count?: number;
    runtime?: string;
  };
  const items = Array.isArray(p.loops) ? p.loops : [];
  const rows = items.slice(0, 10).map((loop) => [
    String(loop.loop_id ?? "?"),
    String(loop.status ?? ""),
    `${String(loop.current_iteration ?? 0)}/${String(loop.max_iterations ?? "∝")}`,
    trunc(String(loop.research_goal ?? ""), 44),
  ]);
  const active = typeof p.active_count === "number" ? p.active_count : 0;
  return { rows, active, runtime: p.runtime };
}

export function loopStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push("loop", p.loop_id);
  push("status", p.status);
  if (p.found === false) {
    // Owner-scoped loop routes (V-13): not yours, or no such loop.
    push("note", p.message);
    return rows;
  }
  if (p.current_iteration !== undefined || p.max_iterations !== undefined) {
    push("iterations", `${String(p.current_iteration ?? 0)}/${String(p.max_iterations ?? "∝")}`);
  }
  push("phase", p.phase);
  push("project", p.project_id);
  push("runtime", p.runtime);
  return rows;
}

export function loopIterationRows(payload: unknown): string[][] {
  const p = (payload ?? {}) as { recent_iterations?: Array<Record<string, unknown>> };
  const items = Array.isArray(p.recent_iterations) ? p.recent_iterations : [];
  return items.slice(0, 5).map((it, idx) => {
    const marker = String(it.iteration ?? it.step ?? it.n ?? idx + 1);
    const summary = trunc(String(it.hypothesis ?? it.observation ?? it.finding ?? it.summary ?? ""), 56);
    return [marker, summary];
  });
}

export function sessionViewRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push("project", p.project_id);
  push("runtime", p.runtime);
  push("loops", sessionViewLoops(p.loops));
  push("sessions", sessionViewSessions(p.sessions));
  if (typeof p.knowledge === "object" && p.knowledge !== null) {
    const k = p.knowledge as Record<string, unknown>;
    if (k.nodes !== undefined || k.edges !== undefined) {
      push("knowledge", `${String(k.nodes ?? "?")} nodes / ${String(k.edges ?? "?")} edges`);
    }
  }
  push("authority", p.authoritative_runtime ?? p.runtime);
  return rows;
}

const ACTIVE_LOOP_STATUSES = ["running", "paused", "pending"];

/** research_session.view sends loops as {count, active_count, items}
 * (session_view._loops_snapshot); arrays are the older shape. The array
 * check alone never showed the row (TUI audit 2026-10-17). */
function sessionViewLoops(loops: unknown): string | undefined {
  if (Array.isArray(loops)) {
    const running = (loops as Array<Record<string, unknown>>).filter(
      (l) => ACTIVE_LOOP_STATUSES.includes(String(l.status ?? "")),
    ).length;
    return `${loops.length} (${running} active)`;
  }
  if (typeof loops !== "object" || loops === null) return undefined;
  const l = loops as { count?: unknown; active_count?: unknown };
  return `${String(l.count ?? 0)} (${String(l.active_count ?? 0)} active)`;
}

/** Durable sessions arrive as {available, count, items} or, when the venue
 * has no durable store, {available: false, reason}. */
function sessionViewSessions(sessions: unknown): string | undefined {
  if (Array.isArray(sessions)) return `${sessions.length}`;
  if (typeof sessions !== "object" || sessions === null) return undefined;
  const s = sessions as { available?: unknown; reason?: unknown; count?: unknown };
  if (s.available === false) return `unavailable (${String(s.reason ?? "no durable store")})`;
  return String(s.count ?? 0);
}

/** Knowledge graph status projection. */
export function knowledgeStatusRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as Record<string, unknown>;
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ key, value: trunc(String(value), 64) });
  };
  push("available", p.available === undefined ? undefined : p.available ? "yes" : "no");
  push("nodes", p.nodes);
  push("edges", p.edges);
  push("project", p.project_id);
  push("runtime", p.runtime);
  push("backends", Array.isArray(p.backends) ? (p.backends as string[]).join(",") : undefined);
  if (p.reason) push("note", p.reason);
  return rows;
}

function listLength(value: unknown): number | undefined {
  return Array.isArray(value) ? value.length : undefined;
}

/** Knowledge inventory rows from the get_knowledge surface: stats is
 * KnowledgeBase.get_stats (patterns, anti_patterns, lessons,
 * avg_confidence). The card showed invented "nodes 0 / edges 0" rows and
 * capped counts at the 20-row list length (TUI audit 2026-10-17); nodes,
 * edges and runtime show only when a payload carries them. */
export function knowledgeInventoryRows(payload: unknown): MgmtRow[] {
  const p = (payload ?? {}) as { stats?: Record<string, unknown>; patterns?: unknown; lessons?: unknown };
  const stats = p.stats ?? {};
  const rows: MgmtRow[] = [];
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ key, value: String(value) });
  };
  push("nodes", stats.nodes);
  push("edges", stats.edges);
  push("patterns", stats.patterns ?? listLength(p.patterns));
  push("anti-patterns", stats.anti_patterns);
  push("lessons", stats.lessons ?? listLength(p.lessons));
  if (typeof stats.avg_confidence === "number") push("avg confidence", stats.avg_confidence.toFixed(2));
  push("runtime", stats.backend);
  return rows;
}

/** Registered knowledge graph list rows. GraphRegistry rows name the owner
 * `owner_id`; `owner` is the older shape. */
export function knowledgeGraphListRows(payload: unknown): string[][] {
  const p = (payload ?? {}) as { graphs?: Array<Record<string, unknown>> };
  const items = Array.isArray(p.graphs) ? p.graphs : [];
  return items.slice(0, 10).map(graphListRow);
}

/** First present (non-null) field among `keys`, as text. */
function firstText(g: Record<string, unknown>, keys: string[], fallback = ""): string {
  const key = keys.find((k) => g[k] !== undefined && g[k] !== null);
  return key ? String(g[key]) : fallback;
}

function graphListRow(g: Record<string, unknown>): string[] {
  return [
    firstText(g, ["graph_id", "id"], "?"),
    firstText(g, ["kind"]),
    firstText(g, ["owner", "owner_id"]),
    trunc(firstText(g, ["description"]), 40),
  ];
}

function pushKv(rows: MgmtRow[], key: string, value: unknown): void {
  if (value === undefined || value === null || value === "") return;
  rows.push({ key, value: trunc(String(value), 64) });
}

function graphNodeRow(node: Record<string, unknown>): string[] {
  return [trunc(firstText(node, ["label", "name", "id"], "?"), 48), firstText(node, ["type"])];
}

/** One graph's content (GraphRegistry.graph_content: graph_id, name, kind,
 * project_id, owner_id, nodes, edges, truncated). `/graph <id>` used the
 * list renderer, which dropped nodes and edges (TUI audit 2026-10-17). */
export function knowledgeGraphContentRows(payload: unknown): { header: MgmtRow[]; nodes: string[][] } {
  const p = (payload ?? {}) as Record<string, unknown>;
  const nodes = Array.isArray(p.nodes) ? (p.nodes as Array<Record<string, unknown>>) : [];
  const edges = listLength(p.edges) ?? 0;
  const header: MgmtRow[] = [];
  pushKv(header, "graph", p.name ?? p.graph_id);
  pushKv(header, "id", p.graph_id);
  pushKv(header, "kind", p.kind);
  pushKv(header, "owner", p.owner_id ?? p.owner);
  pushKv(header, "project", p.project_id);
  pushKv(header, "content", `${nodes.length} nodes, ${edges} edges${p.truncated ? " (truncated)" : ""}`);
  return { header, nodes: nodes.slice(0, 15).map(graphNodeRow) };
}

/** Flatten the freeform describe payload into display rows. */
export function wfDescribeRows(payload: unknown): MgmtRow[] {
  const rows: MgmtRow[] = [];
  const p = (payload ?? {}) as Record<string, unknown>;
  for (const [key, value] of Object.entries(p)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      rows.push({ key, value: `${value.length} item(s)` });
      continue;
    }
    if (typeof value === "object") continue;
    rows.push({ key, value: trunc(String(value), 60) });
  }
  return rows.slice(0, 12);
}
