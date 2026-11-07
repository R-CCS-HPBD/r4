/**
 * Team plan live events (ADR-092 slice 3, "surfaces").
 *
 * Pure logic for team plan runs in the console: the run-keyed team_event
 * line the bridge pushes (`run_id` + agent id, see the bridge's
 * team_plan_events), the Work pane's plan-run row, and the agent_result
 * handler's per-agent result lines. No bridge/store imports — unit-testable
 * in isolation.
 *
 * Honesty contract (as team-live.ts): a row or line is built ONLY from what
 * the bridge reported; absent pieces stay undefined, never invented.
 */

export interface PlanEventLine {
  run_id: string;
  plan?: string;
  /** plan_started | phase | agent_started | agent_round | agent_tool |
   * agent_result | agent_failed | agent_cancelled | gate_requested |
   * gate_answered | budget_warning | budget_exhausted | plan_log | plan_finished */
  kind: string;
  /** The agent id (`<step>[<item>]#<replica>`); "" for run/phase facts. */
  agent?: string;
  emitted_at?: string;
  step?: string | null;
  phase?: string | null;
  title?: string;
  model?: string | null;
  replayed?: boolean;
  ok?: boolean | null;
  reason?: string | null;
  tokens?: number | null;
  preview?: string;
  refused_tools?: string[];
  round?: number;
  input_tokens?: number;
  output_tokens?: number;
  tool?: string;
  state?: string;
  duration_ms?: number | null;
  gate?: string;
  class?: string;
  prompt?: string;
  approved?: boolean;
  answered?: boolean;
  status?: string;
  error?: string | null;
  spent_tokens?: number | null;
  agents_started?: number | null;
  result_path?: string;
  agent_bound?: number;
  attempt?: string;
  text?: string;
}

/** One TeamService run as `team.runs` lists it (kind plan | spec | team). */
export interface TeamRunRow {
  kind: "plan" | "spec" | "team" | string;
  run_id: string;
  name?: string | null;
  engine?: string;
  status?: string;
  project_id?: string | null;
  attempt?: string;
  phase?: string | null;
  agents?: { started?: number | null; ok?: number | null; null?: number | null; roles?: string[] };
  cost?: { tokens?: number | null; llm_calls?: number | null };
  error?: string | null;
  updated_at?: string | null;
  live?: boolean;
}

export const PLAN_RESULT_KINDS = new Set(["agent_result", "agent_failed", "agent_cancelled"]);
const RING_CAP = 300;
const PREVIEW_CLIP = 120;

/** A plan line carries a run id and no teamup session id. */
export function isPlanEvent(event: unknown): event is PlanEventLine {
  if (!event || typeof event !== "object") return false;
  const line = event as Record<string, unknown>;
  return typeof line.run_id === "string" && line.run_id !== "" && !line.session_id;
}

export function appendPlanEvent(ring: PlanEventLine[], line: PlanEventLine): PlanEventLine[] {
  const next = [...ring, line];
  return next.length > RING_CAP ? next.slice(next.length - RING_CAP) : next;
}

/** The run's row after one event (a new row when the run is not listed yet). */
export function applyPlanEvent(row: TeamRunRow | undefined, event: PlanEventLine): TeamRunRow {
  const base: TeamRunRow = row ?? { kind: "plan", run_id: event.run_id, status: "running" };
  const next: TeamRunRow = { ...base, name: base.name ?? event.plan ?? null };
  const agents = { ...(base.agents ?? {}) };
  const cost = { ...(base.cost ?? {}) };
  if (event.kind === "plan_started") {
    next.status = "running";
    next.live = true;
  } else if (event.kind === "phase") {
    next.phase = event.phase ?? null;
  } else if (event.kind === "agent_started") {
    agents.started = (agents.started ?? 0) + 1;
  } else if (PLAN_RESULT_KINDS.has(event.kind)) {
    if (event.kind === "agent_result") agents.ok = (agents.ok ?? 0) + 1;
    else agents.null = (agents.null ?? 0) + 1;
    cost.tokens = (cost.tokens ?? 0) + (event.tokens ?? 0);
  } else if (event.kind === "plan_finished") {
    next.status = event.status ?? next.status;
    next.live = false;
    if (event.spent_tokens != null) cost.tokens = event.spent_tokens;
    if (event.error) next.error = event.error;
  }
  next.agents = agents;
  next.cost = cost;
  if (event.emitted_at) next.updated_at = event.emitted_at;
  return next;
}

/** The agent_result handler's line for one finished agent (✓ ok · ✖ null · ⊘ cancelled). */
export function agentResultLine(event: PlanEventLine): string {
  const agent = event.agent || "?";
  const tokens = event.tokens ? ` · ${formatTokens(event.tokens)}` : "";
  const replayed = event.replayed ? " · replayed" : "";
  if (event.kind === "agent_result") {
    const preview = clip(event.preview ?? "");
    return `✓ ${agent}${tokens}${replayed}${preview ? ` — ${preview}` : ""}`;
  }
  if (event.kind === "agent_cancelled") return `⊘ ${agent} · cancelled`;
  const reason = event.reason ? clip(event.reason, 80) : "null";
  const refused = event.refused_tools?.length ? ` · refused ${event.refused_tools.join(",")}` : "";
  return `✖ ${agent} · ${reason}${refused}${tokens}`;
}

/** Per-agent result lines of a run (newest answer per agent, run order kept). */
export function agentResultLines(ring: PlanEventLine[]): string[] {
  const latest = new Map<string, PlanEventLine>();
  for (const line of ring) {
    if (PLAN_RESULT_KINDS.has(line.kind)) latest.set(line.agent || "?", line);
  }
  return [...latest.values()].map(agentResultLine);
}

/** One short line for a non-result event (notice details / overlay). */
export function planEventLine(event: PlanEventLine): string {
  if (PLAN_RESULT_KINDS.has(event.kind)) return agentResultLine(event);
  const agent = event.agent ? `${event.agent} ` : "";
  switch (event.kind) {
    case "plan_started":
      return `started ${event.attempt ?? ""}${event.agent_bound ? ` · ≤${event.agent_bound} agents` : ""}`.trim();
    case "phase":
      return `phase ${event.phase ?? "?"}${event.title ? `: ${event.title}` : ""}`;
    case "agent_started":
      return `${agent}started${event.model ? ` on ${event.model}` : ""}${event.replayed ? " (replay)" : ""}`;
    case "agent_round":
      return `${agent}round ${event.round ?? "?"} · ${formatTokens((event.input_tokens ?? 0) + (event.output_tokens ?? 0))}`;
    case "agent_tool":
      return `${agent}${event.tool ?? "tool"} ${event.state ?? ""}`.trim();
    case "gate_requested":
      return `gate ${event.gate ?? "?"} [${event.class ?? "?"}] asks you — ${clip(event.prompt ?? "")}`;
    case "gate_answered":
      return `gate ${event.gate ?? "?"} → ${event.approved ? "approved" : event.answered ? "rejected" : `refused${event.reason ? ` (${event.reason})` : ""}`}`;
    case "budget_warning":
      return "budget: 80% of the token ceiling spent";
    case "budget_exhausted":
      return clip(event.text ?? "budget exhausted");
    case "plan_log":
      return clip(event.text ?? "");
    case "plan_finished":
      return `finished ${event.status ?? "?"}${event.spent_tokens != null ? ` · ${formatTokens(event.spent_tokens)}` : ""}`;
    default:
      return event.kind.replace(/_/g, " ");
  }
}

/** The Work pane label of a plan run: name · status · agents · cost · phase. */
export function planRowLabel(row: TeamRunRow): string {
  const parts = [`plan ${row.name ?? row.run_id}`, row.status ?? "unknown"];
  const ok = row.agents?.ok ?? 0;
  const nulls = row.agents?.null ?? 0;
  if (row.agents && (ok || nulls || row.agents.started)) {
    parts.push(`agents ${ok}✓${nulls ? ` ${nulls}✖` : ""}`);
  }
  if (row.cost?.tokens != null) parts.push(formatTokens(row.cost.tokens));
  if (row.phase && row.status === "running") parts.push(`@${row.phase}`);
  return parts.join(" · ");
}

/** The agents chip of an interactive team row (team.runs or teamup.list).
 * ADR-092 slice 4 retired the team's compute budget: no budget chip. */
export function teamAgentChips(roles?: string[] | null): string {
  return roles && roles.length ? `${roles.length} agents` : "";
}

export function planRunIsTerminal(status?: string): boolean {
  return status === "success" || status === "partial" || status === "failed" || status === "cancelled";
}

export function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M tok`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k tok`;
  return `${tokens} tok`;
}

function clip(text: string, cap = PREVIEW_CLIP): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= cap ? flat : `${flat.slice(0, cap - 1)}…`;
}
